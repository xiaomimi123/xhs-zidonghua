"""用户端接口:选赛道 → 采集结果 → 拆解 → 复刻 → 导出。不涉及任何接口配置与密钥。"""
import io
import json
import re
import secrets
import threading
import time
import zipfile
from urllib.parse import quote
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from . import llm, worker, exporter, imagegen, compliance, mirror, lengthcap, autorun, products, qianfan, loop, thumbs, ptype, version, product_check
from .auth import (require_user, user_ok, is_admin, check_pw, hash_pw, make_token, make_staff_token, current_staff, staff_mode, login_required,
                   throttle, CURRENT, SESSION_DAYS)
from .api_collector import collector_summary
from .notes import annotate, localize
from .db import get_settings, q_all, q_one, q_exec, conn, tier_sql
from .scope import check as own, my_id, owner_sql, visible

public = APIRouter(prefix="/api")
router = APIRouter(dependencies=[Depends(require_user)])


def _one(sql, *a):
    r = q_one(sql, *a)
    if not r:
        raise HTTPException(404, "不存在")
    return r


# ---------- 访问密码 ----------
class LoginIn(BaseModel):
    username: str = ""
    password: str


_DUMMY_HASH = hash_pw("dummy-password-for-timing")


@public.get("/version")
def version_info():
    """当前部署的版本号(页面左下角显示;回滚时对照用)。不需要登录。"""
    return version.info()


@public.get("/session")
def session(request: Request):
    s = current_staff(request)
    return {"ok": user_ok(request), "need_password": login_required(), "staff_mode": staff_mode(), "is_admin": is_admin(request),
            "user": {"name": s["name"] or s["username"], "username": s["username"]} if s else None}


@public.post("/login")
def login(d: LoginIn, request: Request, response: Response):
    """有员工账号时用「账号 + 密码」登录;没建过员工账号时沿用旧的通用访问密码。"""
    throttle(request, None)
    username = d.username.strip()
    if staff_mode():
        st = q_one("SELECT * FROM staff WHERE username=?", username) if username else None
        ok = check_pw(d.password, st["password_hash"] if st else _DUMMY_HASH) and bool(st)
        throttle(request, ok)
        if not ok:
            raise HTTPException(400, "账号或密码不正确")
        if not st["enabled"]:
            raise HTTPException(403, "这个账号已停用,请联系管理员")
        with conn() as c:
            c.execute("UPDATE staff SET last_login=datetime('now','localtime'), last_ip=? WHERE id=?",
                      (request.client.host if request.client else "", st["id"]))
        response.set_cookie("usr", make_staff_token(st), max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax")
        return {"ok": True}
    ok = check_pw(d.password, get_settings()["user_password_hash"])
    throttle(request, ok)
    if not ok:
        raise HTTPException(400, "密码不正确")
    response.set_cookie("usr", make_token("user"), max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax")
    return {"ok": True}


@public.post("/logout")
def logout(response: Response):
    response.delete_cookie("usr")
    return {"ok": True}


class MyPw(BaseModel):
    old: str
    new: str


@router.post("/api/me/password")
def change_my_password(d: MyPw, request: Request, response: Response):
    """员工改自己的密码:要输对原密码;改完其他设备上的登录全部失效,当前这台换新会话继续用。"""
    st = current_staff(request)
    if not st:
        raise HTTPException(400, "只有员工账号可以在这里改密码")
    throttle(request, None)
    row = q_one("SELECT * FROM staff WHERE id=?", st["id"])
    ok = check_pw(d.old, row["password_hash"])
    throttle(request, ok)
    if not ok:
        raise HTTPException(400, "原密码不正确")
    if len(d.new) < 6:
        raise HTTPException(400, "新密码至少 6 位")
    if d.new == d.old:
        raise HTTPException(400, "新密码不能和原密码一样")
    q_exec("UPDATE staff SET password_hash=?, token_ver=token_ver+1 WHERE id=?", hash_pw(d.new), st["id"])
    response.set_cookie("usr", make_staff_token(q_one("SELECT * FROM staff WHERE id=?", st["id"])), max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax")
    return {"ok": True}


# ---------- 赛道(只读,由管理端维护) ----------
@router.get("/api/tracks")
def tracks():
    return q_all("SELECT name, keywords FROM tracks WHERE enabled=1 ORDER BY sort, id")


# ---------- 项目 ----------
task_opts = autorun.task_opts


def split_keywords(raw: list[str]) -> list[str]:
    """一个框里用逗号写多个词也拆开;关键词是并列的搜索词,最多 5 个。"""
    return list(dict.fromkeys(k.strip() for r in raw for k in re.split(r"[,，、;；]+", r) if k.strip()))[:5]


class ProjectEdit(BaseModel):
    keywords: list[str]
    direction: str = ""


class ProjectIn(BaseModel):
    track: str
    keywords: list[str]
    direction: str = ""


@router.get("/api/projects")
def list_projects():
    return q_all("""SELECT p.*,
        (SELECT COUNT(*) FROM project_notes WHERE project_id=p.id) AS note_count,
        (SELECT COUNT(*) FROM drafts WHERE project_id=p.id) AS draft_count,
        (SELECT status FROM autoruns WHERE project_id=p.id) AS auto_status,
        (SELECT COUNT(*) FROM crawl_tasks WHERE project_id=p.id AND status IN ('pending','running')) AS crawling
        FROM projects p WHERE 1=1""" + owner_sql()[0] + " ORDER BY id DESC", *owner_sql()[1])


def _pick_account(account_id: str) -> tuple[str, str]:
    """用户在新建任务时选的发布账号:必须是千帆里读到的账号,空 = 不指定。"""
    aid = (account_id or "").strip()
    if not aid:
        return "", ""
    name = qianfan.account_name(aid)
    if name is None:
        raise HTTPException(400, "找不到这个发布账号,请刷新页面重新选择")
    return aid, name


def _account_pref(account: tuple[str, str], angles: list[str]) -> tuple[str, str]:
    """任务指定账号后:选题角度偏好(用户在页面上选的,只认固定的那套)+ 账号里表现好的标题(当时的快照,任务期间不变)。"""
    from .llm import ANGLES
    pref = ",".join(a for a in dict.fromkeys(angles) if a in ANGLES)
    style = "\n".join(f"- {t}" for t in qianfan.style_titles(account[0])) if account[0] else ""
    return pref, style


def _new_project(track: str, kws: list[str], direction: str, with_tasks: bool = True, account: tuple[str, str] = ("", ""),
                 angles: list[str] | None = None) -> int:
    lim = int(get_settings()["collect_limit"])
    pref, style = _account_pref(account, angles or [])
    with conn() as c:
        who = (CURRENT.get() or {}).get("username", "")
        pid = c.execute("INSERT INTO projects(track,keywords,direction,account_id,account_name,angle_pref,account_style,created_by,owner_id) VALUES(?,?,?,?,?,?,?,?,?)",
                        (track, json.dumps(kws, ensure_ascii=False), direction, *account, pref, style, who, my_id())).lastrowid
        for k in kws if with_tasks else []:
            c.execute("INSERT INTO crawl_tasks(project_id,type,query,lim,max_age_days,note_type,sort) VALUES(?,?,?,?,?,?,?)",
                      (pid, "keyword", k, lim, *task_opts()))
    return pid


@router.post("/api/projects")
def create_project(p: ProjectIn):
    kws = split_keywords(p.keywords)
    if not p.track.strip() or not kws:
        raise HTTPException(400, "请选择赛道并至少填写一个关键词")
    return {"id": _new_project(p.track.strip(), kws, p.direction)}


class ProductIn(BaseModel):
    name: str
    images: list[str]
    price: str = ""
    selling_points: str = ""
    audience: str = ""
    forbid: str = ""
    size_note: str = ""    # 产品实际大小(可选,不填由 AI 按品类和规格估)


class AutoIn(BaseModel):
    track: str = ""
    keywords: list[str] = []
    count: int = 10
    direction: str = ""
    product_id: int | None = None
    product: ProductIn | None = None
    account_id: str = ""   # 这批稿子打算发到哪个账号(千帆账号),可不选
    angles: list[str] = []   # 选题角度偏好(固定的 9 类之一),通常是页面按账号数据推荐、用户确认过的
    mix: dict[str, int] = {}   # 帖子类型比例 {"产品软文": 6, "测评": 4};不填就按默认(有产品:软文+测评)


@router.post("/api/auto")
def auto_start(a: AutoIn):
    """一键批量生成:选好赛道、填好篇数就行,关键词可以不填(取赛道预设词,宽泛的词自动换成更具体的)。"""
    s = get_settings()
    track = a.track.strip()
    if not (s.get("deepseek_api_key") or "").strip():
        raise HTTPException(400, "管理端还没有配置 DeepSeek Key,无法自动生成")
    count = max(1, min(a.count, int(s.get("auto_max_count") or 50)))
    acc = _pick_account(a.account_id)
    if a.product_id or a.product:  # 有产品:先识别产品图,赛道和关键词由系统定(也可以手动指定赛道)
        if a.product_id:
            prod = products.get(a.product_id)
            if not prod:
                raise HTTPException(400, "找不到这个产品")
            ppid = prod["id"]
        else:
            try:
                ppid = products.create(a.product.name, a.product.images, a.product.price, a.product.selling_points,
                                       a.product.audience, a.product.forbid, a.product.size_note)
            except ValueError as e:
                raise HTTPException(400, str(e))
        pref, style = _account_pref(acc, a.angles)
        pid = q_exec("INSERT INTO projects(track,keywords,direction,product_id,account_id,account_name,angle_pref,account_style,created_by,owner_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     track, json.dumps(split_keywords(a.keywords), ensure_ascii=False), a.direction.strip()[:500], ppid, *acc, pref, style,
                     (CURRENT.get() or {}).get("username", ""), my_id()).lastrowid
        autorun.new_run(pid, count, "profiling", mix=ptype.parse_mix(a.mix))
        return {"id": pid, "keywords": [], "count": count, "product_id": ppid}
    if not track:
        raise HTTPException(400, "请先选择赛道")
    given = split_keywords(a.keywords)
    if not given and acc[0]:   # 没填关键词、但选了账号:用按这个账号的内容画像推荐的搜索词(AI 不可用就回到赛道预设词)
        try:
            given = qianfan.account_keywords(acc[0])[:int(s.get("auto_default_keywords") or 3)]
        except Exception:
            given = []
    kws = autorun.refine_keywords(track, given, int(s.get("auto_default_keywords") or 3))
    if not kws:
        raise HTTPException(400, "这个赛道没有预设关键词,请至少填写一个")
    pid = _new_project(track, kws, a.direction.strip()[:500], with_tasks=False, account=acc, angles=a.angles)
    src = autorun.source(pid, kws, s)  # 笔记库优先:够了就不采集
    autorun.new_run(pid, count, "collecting", autorun.source_msg(src), library=src["library"], mix=ptype.parse_mix(a.mix))
    return {"id": pid, "keywords": kws, "count": count, "library": src["library"], "collect": src["collect"]}


@router.get("/api/products")
def list_products():
    return products.listing()


class ProductEdit(BaseModel):
    name: str
    price: str = ""
    selling_points: str = ""
    audience: str = ""
    forbid: str = ""
    size_note: str | None = None   # 产品实际大小;不传 = 不改
    keep: list[str] = []   # 保留的原图文件名
    add: list[str] = []    # 新上传的图(data URL)
    order: list[str] = []  # 页面上的图片顺序:"k:文件名" 原图、"a:序号" 新上传的第几张(可以交错);不传就原图在前


@router.post("/api/products")
def create_product(p: ProductIn):
    """新建产品(名称 + 产品图 + 可选资料),返回产品 id。Live 页「产品图生成场景」上传新图时用:先有产品资料,场景、搭配、不要出现的东西、实际大小才有依据。"""
    try:
        pid = products.create(p.name, p.images, p.price, p.selling_points, p.audience, p.forbid, p.size_note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    threading.Thread(target=lambda: products.get_spec(products.get(pid)), daemon=True, name="product-spec").start()   # 后台先把拍摄规格生成好,选场景时就不用等
    return {"id": pid}


class ProductCheckIn(BaseModel):
    name: str = ""
    images: list[str] = []         # 新上传的图(data URL)
    keep: list[str] = []           # 编辑产品时保留的原图文件名
    order: list[str] = []          # 页面上的图片顺序(同 ProductEdit.order),检查提示里的「第几张」按它算
    selling_points: str = ""
    audience: str = ""
    forbid: str = ""
    product_id: int = 0            # 编辑时传:查重复图时不和自己比


@router.post("/api/products/check")
def check_product(c: ProductCheckIn):
    """提交产品前的素材检查:传错图、混进别的产品的图、文案像指令 / 复制了别的产品的、太长被截断…只返回提醒,由页面让用户选继续还是回去改。"""
    try:
        new = products.decode_images(c.images) if c.images else []
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"warnings": product_check.check(c.name, new, c.keep, c.selling_points, c.audience, c.forbid, c.product_id, order=c.order)}


@router.put("/api/products/{pid}")
def edit_product(pid: int, e: ProductEdit):
    try:
        products.update(pid, e.name, e.price, e.selling_points, e.audience, e.forbid, e.keep, e.add, e.order, e.size_note)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {"ok": True}


@router.delete("/api/products/{pid}")
def del_product(pid: int):
    """删除产品和它的图片。用这个产品做出来的稿子保留;正在跑的任务用着它时不能删。"""
    busy = q_one(f"SELECT COUNT(*) c FROM autoruns a JOIN projects p ON p.id=a.project_id WHERE p.product_id=? AND a.status IN ({','.join('?' * len(autorun.ACTIVE))})",
                 pid, *autorun.ACTIVE)["c"]
    if busy:
        raise HTTPException(400, "有任务正在用这个产品,等它跑完或取消后再删")
    products.delete(pid)
    return {"ok": True}


@router.get("/product_images/{name}")
def product_image(name: str, w: int = 0):
    p = products.DIR / Path(name).name
    if not p.is_file():
        raise HTTPException(404)
    return _img_response(p, w, "private, max-age=86400")


# ---------- 流水线界面:任务列表、任务详情、成品库 ----------
STEP_NAMES = {"profiling": "识别产品", "collecting": "采集", "tagging": "分类", "analyzing": "拆解", "writing": "写稿配图", "retrying": "质检"}


def _staff_names() -> dict[str, str]:
    return {r["username"]: r["name"] for r in q_all("SELECT username, name FROM staff")}


def _who(created_by: str, names: dict[str, str]) -> str:
    """发起人显示名:员工用姓名(没填姓名用登录名);没有记录的是管理员发起的。"""
    cb = (created_by or "").strip()
    return (names.get(cb) or cb) if cb else "管理员"


# 任务发起人的 SQL 写法(p = projects,s = 用 p.created_by 关联的 staff)
_WHO_SQL = "COALESCE(NULLIF(s.name,''), NULLIF(p.created_by,''), '管理员')"


def _run_info(p: dict, pre: tuple | None = None, names: dict | None = None) -> dict:
    """一个一键生成任务的概况:名称、阶段条、进度数字、封面图。pre = (进度, 产品, 封面稿子的图片列表):批量查好了就传进来。"""
    if pre:
        pr, prod, imgs = pre
        pr = pr or {}
    else:
        pr = autorun.progress(p["id"]) or {}
        prod = products.get(p.get("product_id"))
        img = q_one("SELECT images FROM drafts WHERE project_id=? AND images!='[]' ORDER BY auto_ok DESC, id LIMIT 1", p["id"])
        imgs = img["images"] if img else []
    steps = [s for s in autorun.STEPS if s in STEP_NAMES and (prod or s != "profiling")]
    status = pr.get("status", "")
    at = status if status in steps else pr.get("failed_at") if status in ("error", "canceled") else ""
    idx = len(steps) if status == "done" else (steps.index(at) if at in steps else 0)
    cover = next((f"/images/{i}" for i in (imgs or []) if i), "")
    if not cover and prod and prod["images"]:
        cover = f"/product_images/{prod['images'][0]}"
    kws = p.get("keywords") or []
    return {"id": p["id"], "name": prod["name"] if prod else f"{p['track']} · {kws[0] if kws else ''}".strip(" ·"),
            "kind": "product" if prod else "track", "track": p["track"], "keywords": kws, "created": p["created"],
            "target": pr.get("target", 0), "status": status, "msg": pr.get("msg", ""), "active": pr.get("active", False),
            "steps": [STEP_NAMES[s] for s in steps], "step": idx, "tasks": pr.get("tasks", [0, 0]), "notes": pr.get("notes", 0),
            "playbooks": pr.get("playbooks", [0, 0]), "drafts": pr.get("drafts", {}), "cover": cover,
            "library": pr.get("library", 0), "refs": pr.get("refs", 0), "reused": pr.get("reused", 0),
            "by": _who(p.get("created_by"), names if names is not None else _staff_names())}


def _runs_info(ps: list[dict]) -> list[dict]:
    """一批任务的概况:进度、产品、封面各查一次(原来每个任务查 8 条左右,任务越多越慢)。"""
    if not ps:
        return []
    pids = [p["id"] for p in ps]
    prs = autorun.progress_many(pids)
    prods = {r["id"]: r for r in autorun._in_chunks("SELECT * FROM products WHERE id IN ({})", {p["product_id"] for p in ps if p.get("product_id")})}
    covers = {r["project_id"]: r["images"] for r in autorun._in_chunks(
        "SELECT project_id, images FROM (SELECT project_id, images, ROW_NUMBER() OVER (PARTITION BY project_id ORDER BY auto_ok DESC, id) AS rn "
        "FROM drafts WHERE project_id IN ({}) AND images!='[]') WHERE rn=1", pids)}
    names = _staff_names()
    return [_run_info(p, (prs.get(p["id"]), prods.get(p.get("product_id")), covers.get(p["id"])), names) for p in ps]


@router.get("/api/runs")
def list_runs(limit: int = 100, offset: int = 0, by: str = ""):
    """最近的任务,从 offset 起取 limit 个(默认 100,最多 500)。任务页按页取,不一次取全部。by = 只看某个发起人的(管理员能看到所有人的任务)。"""
    limit = min(max(int(limit or 100), 1), 500)
    offset = max(int(offset or 0), 0)
    w, a = owner_sql()
    if by:
        w += f" AND {_WHO_SQL}=?"; a = [*a, by]
    return _runs_info(q_all("SELECT p.* FROM projects p JOIN autoruns a ON a.project_id=p.id LEFT JOIN staff s ON s.username=p.created_by WHERE 1=1" + w
                            + " ORDER BY a.id DESC LIMIT ? OFFSET ?", *a, limit, offset))


@router.get("/api/runs/count")
def runs_count(by: str = ""):
    """任务总数(by 不空时只算这个发起人的)和各发起人的任务数(不受 by 影响,筛选按钮要一直在)。"""
    w, a = owner_sql()
    base = "FROM autoruns x JOIN projects p ON p.id=x.project_id LEFT JOIN staff s ON s.username=p.created_by WHERE 1=1" + w
    who = {r["w"]: r["n"] for r in q_all(f"SELECT {_WHO_SQL} AS w, COUNT(*) n {base} GROUP BY w ORDER BY n DESC", *a)}
    total = q_one(f"SELECT COUNT(*) c {base}" + (f" AND {_WHO_SQL}=?" if by else ""), *a, *([by] if by else []))["c"]
    return {"total": total, "who": who}


@router.post("/api/runs/{pid}/rerun")
def rerun(pid: int):
    """用同样的设置再跑一次(新建一个任务,不动原来的):产品任务会重新识别产品图、重新定关键词;赛道任务沿用关键词。"""
    p = _one("SELECT * FROM projects WHERE id=?", pid)
    run = _one("SELECT target, status FROM autoruns WHERE project_id=?", pid)
    if run["status"] in autorun.ACTIVE:
        raise HTTPException(400, "这个任务还在进行中,等它结束或取消后再重新运行")
    prod = bool(p["product_id"])
    return auto_start(AutoIn(track="" if prod else p["track"], keywords=[] if prod else list(p["keywords"] or []), count=run["target"] or 10,
                             direction="" if prod else (p["direction"] or ""), product_id=p["product_id"] or None,
                             account_id=p.get("account_id") or "", angles=[x for x in (p.get("angle_pref") or "").split(",") if x]))


@router.get("/api/runs/{pid}")
def run_detail(pid: int):
    p = _one("SELECT * FROM projects WHERE id=?", pid)
    info = _run_info(p)
    prod = products.get(p.get("product_id"))
    info["category"] = (prod or {}).get("category") or ""
    info["relevant"] = autorun.relevant_count(pid, get_settings()) if info["notes"] else 0
    return info


# 列表里不要的大字段:正文、备选标题、标签、检查说明、千帆表现。点开预览时再按需取(/api/library/{id})
_LIB_HEAVY = ("alt_titles", "content", "tags", "auto_note", "perf")


def _library(pid: int = 0, did: int = 0, brief: bool = False) -> list[dict]:
    where, args = owner_sql()
    if pid:
        own("project", pid)
        where += " AND d.project_id=?"; args.append(pid)
    if did:
        where += " AND d.id=?"; args.append(did)
    rows = q_all("SELECT d.*, p.track, p.keywords, p.product_id FROM drafts d JOIN projects p ON p.id=d.project_id "
                 "WHERE d.title!='' AND d.status NOT IN ('queued','writing','reviewing','drawing')" + where + " ORDER BY d.id DESC LIMIT 500", *args)
    names = {r["id"]: r["name"] for r in q_all("SELECT id, name FROM products")}
    items, data_at = qianfan.notes_all()
    perf = qianfan.match_drafts(items)   # 只算一次(evaluate 里也要用,原来各算一遍)
    tiers = {r["id"]: r for r in loop.evaluate(register=False, pre=(items, data_at, perf))["rows"]}
    perf_keys = ("views", "likes", "collects", "comments", "shares", "new_fans", "published_at", "account_id", "account_name", "note_id")
    out = [{"id": d["id"], "pid": d["project_id"],
             "run": names.get(d["product_id"]) or f"{d['track']} · {(d['keywords'] or [''])[0]}".strip(" ·"),
             "title": d["title"], "alt_titles": d["alt_titles"], "content": d["content"], "tags": d["tags"],
             "images": [i for i in d["images"] if i], "auto_ok": d["auto_ok"], "auto_note": d["auto_note"],
             "published": bool(d["published"]), "published_at": (d["published_at"] or "")[:10], "linked": bool(d["qf_note_id"]), "grade": (d["review"] or {}).get("grade"), "score": (d["review"] or {}).get("overall"),
             "high": (d["checks"] or {}).get("high", 0), "holes": len((d["checks"] or {}).get("placeholders") or []),
             "status": d["status"], "can_cover": bool((d["cover_card"] or {}).get("layout")),
             "account_id": d["account_id"] or "", "account_name": d["account_name"] or "", "ptype": d["ptype"] or "", "ptype_got": d["ptype_got"] or "",
             "tier": (tiers.get(d["id"]) or {}).get("tier", ""), "lift": (tiers.get(d["id"]) or {}).get("lift"),
             "perf": {k: perf[d["id"]].get(k) for k in perf_keys if perf[d["id"]].get(k) not in (None, "")} if d["id"] in perf else None} for d in rows]
    if brief:
        for it in out:
            for k in _LIB_HEAVY:
                it.pop(k, None)
    return out


@router.get("/api/library")
def library(pid: int = 0, brief: bool = False):
    """成品库:写好的笔记(跨任务),带任务名称、发布标记、发布账号,以及发布后在千帆里对上号的表现数据。
    brief=1 时去掉正文 / 备选标题 / 标签 / 检查说明 / 千帆表现,列表页用(体积小很多)。"""
    return _library(pid, brief=brief)


@router.get("/api/library/{did}")
def library_one(did: int):
    """一篇成品的完整内容(预览弹窗用)。"""
    r = _library(did=did)
    if not r:
        raise HTTPException(404, "没有这篇稿子")
    return r[0]


class AccountIn(BaseModel):
    account_id: str = ""


@router.post("/api/drafts/{did}/account")
def set_draft_account(did: int, p: AccountIn):
    """指定(或更换)这篇稿子要发布到的账号;account_id 为空 = 清除。"""
    _one("SELECT id FROM drafts WHERE id=?", did)
    aid, name = _pick_account(p.account_id)
    q_exec("UPDATE drafts SET account_id=?, account_name=? WHERE id=?", aid, name, did)
    return {"ok": True, "account_id": aid, "account_name": name}


# ---------- 笔记库(员工端只读):采集到的笔记,按赛道 / 主题 / 内容类型 / 分级找参考 ----------
NB_FIELDS = ("id", "title", "content", "author", "likes", "collects", "comments", "published_at", "cover", "images", "tags", "track", "topic",
             "ai_type", "ai_highlight", "ai_audience", "ai_tags", "tier", "note_type", "url", "rel", "old", "dup_of", "kind", "kind_sub", "kind_why", "kind_rel")


@router.get("/api/notebase")
def notebase(track: str = "", topic: str = "", ai_type: str = "", tier: str = "", q: str = "", sort: str = "new", offset: int = 0, limit: int = 36, kind: str = "", kind_sub: str = ""):
    """员工端笔记库:和管理端同一份笔记,只给看笔记需要的字段(不含原始数据、采集端信息、AI 状态)。"""
    from .api_admin import all_notes
    r = all_notes(track=track, topic=topic, ai_type=ai_type, tier=tier, q=q.strip()[:60], sort=sort, offset=offset, limit=min(max(limit, 1), 60), kind=kind, kind_sub=kind_sub)
    return {"total": r["total"], "items": [{k: n.get(k) for k in NB_FIELDS} for n in r["items"]]}


@router.get("/api/notebase/facets")
def notebase_facets(track: str = ""):
    from .api_admin import note_facets
    f = note_facets(track)
    return {k: [x for x in f[k] if x["k"]] for k in ("tracks", "topics", "types", "tiers", "kinds")}


class PubIn(BaseModel):
    on: bool = True


@router.post("/api/drafts/{did}/cover")
def change_cover(did: int):
    """换个封面样式(版式和配色随机换一个,只重做封面)。"""
    d = _one("SELECT cover_card, status FROM drafts WHERE id=?", did)
    if not (d["cover_card"] or {}).get("layout"):
        raise HTTPException(400, "这篇是旧版草稿,没有程序排版的封面")
    if d["status"] in ("queued", "writing", "reviewing", "drawing"):
        raise HTTPException(400, "这篇正在处理中,稍后再换")
    worker.change_cover(did)
    return {"ok": True}


@router.post("/api/drafts/{did}/published")
def set_published(did: int, p: PubIn):
    _one("SELECT id FROM drafts WHERE id=?", did)
    q_exec("UPDATE drafts SET published=?, published_at=CASE WHEN ? THEN datetime('now') ELSE '' END WHERE id=?", int(p.on), int(p.on), did)
    return {"ok": True, "published": p.on}


@router.get("/api/drafts/{did}/images.zip")
def draft_images_zip(did: int):
    """一篇笔记的全部配图打包下载(按发布顺序编号)。"""
    d = _one("SELECT title, images FROM drafts WHERE id=?", did)
    buf, k = io.BytesIO(), 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for img in d["images"]:
            f = imagegen.IMG_DIR / Path(img).name if img else None
            if f and f.is_file():
                k += 1
                z.write(f, f"{k}{f.suffix}")
    if not k:
        raise HTTPException(404, "这篇还没有配图")
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", d["title"])[:40] or f"note_{did}"
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}.zip"})


@router.get("/api/projects/{pid}/auto")
def auto_progress(pid: int):
    return autorun.progress(pid)


@router.post("/api/projects/{pid}/auto/cancel")
def auto_cancel(pid: int):
    autorun.cancel(pid)
    return {"ok": True}


@router.put("/api/projects/{pid}")
def edit_project(pid: int, e: ProjectEdit):
    """修改关键词和内容方向。只改记录,不会新建采集任务;之后拆解、复刻按新的写。"""
    kws = split_keywords(e.keywords)
    if not kws:
        raise HTTPException(400, "至少需要一个关键词")
    _one("SELECT id FROM projects WHERE id=?", pid)
    q_exec("UPDATE projects SET keywords=?, direction=? WHERE id=?", json.dumps(kws, ensure_ascii=False), e.direction.strip()[:500], pid)
    return {"ok": True, "keywords": kws}


@router.delete("/api/runs/{pid}")
def delete_run(pid: int):
    """任务页的「删除」:连同这个任务的采集任务、拆解卡、稿子(成品)一起删,笔记库里的笔记保留。还在跑的任务要先取消。"""
    run = _one("SELECT status FROM autoruns WHERE project_id=?", pid)
    if run["status"] in autorun.ACTIVE:
        raise HTTPException(409, "任务还在进行中,先取消再删除")
    return del_project(pid)


@router.delete("/api/projects/{pid}")
def del_project(pid: int):
    with conn() as c:
        # 帖子在全局笔记库里保留,只解除与该项目的关联
        for t in ("crawl_tasks", "project_notes", "playbooks", "drafts", "autoruns"):
            c.execute(f"DELETE FROM {t} WHERE project_id=?", (pid,))
        c.execute("DELETE FROM projects WHERE id=?", (pid,))
    return {"ok": True}


# ---------- 采集结果 ----------
class CrawlIn(BaseModel):
    type: str = "keyword"  # keyword | title
    query: str


@router.get("/api/projects/{pid}/crawl")
def crawl_status(pid: int):
    s = collector_summary()
    return {"tasks": q_all("SELECT id,type,query,lim,status,error,note_count,created FROM crawl_tasks WHERE project_id=? ORDER BY id DESC", pid),
            "collector": {"online": s["online"], "ago": s["ago"]}}


@router.post("/api/projects/{pid}/crawl")
def add_crawl(pid: int, t: CrawlIn):
    q = t.query.strip()
    if t.type not in ("keyword", "title", "author") or not q:
        raise HTTPException(400, "任务类型或内容不正确")
    if t.type == "author" and (len(q) > 300 or "\n" in q):
        raise HTTPException(400, "博主请填主页链接或小红书号(单行)")
    _one("SELECT id FROM projects WHERE id=?", pid)
    q_exec("INSERT INTO crawl_tasks(project_id,type,query,lim,max_age_days,note_type,sort) VALUES(?,?,?,?,?,?,?)",
           pid, t.type, q[:300 if t.type == "author" else 100], int(get_settings()["collect_limit"]), *task_opts(t.type))
    return {"ok": True}


@router.post("/api/crawl_tasks/{tid}/retry")
def retry_crawl(tid: int):
    q_exec("UPDATE crawl_tasks SET status='pending',error='',worker='' WHERE id=? AND status IN ('failed','done')", tid)
    return {"ok": True}


class KwIn(BaseModel):
    track: str = ""
    word: str


@router.post("/api/keywords/suggest")
def suggest_kw(k: KwIn):
    """用户填了宽泛的搜索词(如「咖啡」)时,推荐更具体、带意图的词。结果缓存 30 天。"""
    w = k.word.strip()[:20]
    if len(w) < 2:
        return {"broad": False, "suggestions": []}
    return autorun.kw_suggest(k.track, w)


@router.get("/api/projects/{pid}/notes")
def list_notes(pid: int):
    rows = q_all(f"""SELECT n.*, {tier_sql()} AS tier FROM project_notes pn JOIN notes n ON n.id=pn.note_id
        WHERE pn.project_id=? ORDER BY n.likes DESC""", pid)
    age = int(get_settings().get("collect_max_age_days") or 0)
    return [annotate(localize(n), age) for n in rows]


@router.delete("/api/projects/{pid}/notes/{nid}")
def unlink_note(pid: int, nid: int):
    """只是从项目里移除,帖子仍在全局笔记库。"""
    q_exec("DELETE FROM project_notes WHERE project_id=? AND note_id=?", pid, nid)
    return {"ok": True}


# ---------- 拆解 ----------
class AnalyzeIn(BaseModel):
    note_ids: list[int]


class PlaybookEdit(BaseModel):
    name: str
    data: dict


@router.get("/api/projects/{pid}/playbooks")
def list_playbooks(pid: int):
    return q_all("SELECT * FROM playbooks WHERE project_id=? ORDER BY id DESC", pid)


@router.post("/api/projects/{pid}/playbooks")
def create_playbook(pid: int, a: AnalyzeIn):
    if not a.note_ids:
        raise HTTPException(400, "请先选择要拆解的笔记")
    cur = q_exec("INSERT INTO playbooks(project_id,name,note_ids) VALUES(?,?,?)",
                 pid, f"拆解 {time.strftime('%m-%d %H:%M')}", json.dumps(a.note_ids[:20]))
    worker.submit_playbook(cur.lastrowid)
    return {"id": cur.lastrowid}


@router.put("/api/playbooks/{bid}")
def edit_playbook(bid: int, e: PlaybookEdit):
    q_exec("UPDATE playbooks SET name=?, data=? WHERE id=?", e.name, json.dumps(e.data, ensure_ascii=False), bid)
    return {"ok": True}


@router.post("/api/playbooks/{bid}/retry")
def retry_playbook(bid: int):
    q_exec("UPDATE playbooks SET status='queued', error='' WHERE id=?", bid)
    worker.submit_playbook(bid)
    return {"ok": True}


@router.delete("/api/playbooks/{bid}")
def del_playbook(bid: int):
    q_exec("DELETE FROM playbooks WHERE id=?", bid)
    return {"ok": True}


# ---------- 复刻 ----------
class GenerateIn(BaseModel):
    playbook_id: int
    count: int = 3
    extra: str = ""
    with_images: bool = True


@router.post("/api/projects/{pid}/generate")
def generate(pid: int, g: GenerateIn):
    pb = _one("SELECT * FROM playbooks WHERE id=? AND project_id=?", g.playbook_id, pid)
    if pb["status"] != "done":
        raise HTTPException(400, "这张拆解卡还没完成")
    return {"ids": worker.plan_and_submit(pid, g.playbook_id, max(1, min(g.count, 50)), g.extra, g.with_images)}


@router.get("/api/projects/{pid}/drafts")
def list_drafts(pid: int):
    return q_all("SELECT * FROM drafts WHERE project_id=? ORDER BY id DESC", pid)


class DraftEdit(BaseModel):
    title: str
    content: str
    tags: list[str]
    image_prompts: list[str]


@router.put("/api/drafts/{did}")
def edit_draft(did: int, e: DraftEdit):
    """保存时重新扫描,让作者补完占位符、改掉违规词后立刻看到结果。"""
    checks = compliance.scan(e.title, e.content, e.tags, e.image_prompts)
    q_exec("UPDATE drafts SET title=?,content=?,tags=?,image_prompts=?,checks=? WHERE id=?",
           e.title, e.content, json.dumps(e.tags, ensure_ascii=False),
           json.dumps(e.image_prompts, ensure_ascii=False), json.dumps(checks, ensure_ascii=False), did)
    return {"ok": True, "checks": checks}


@router.post("/api/drafts/{did}/review")
def review_draft(did: int):
    if not _one("SELECT title FROM drafts WHERE id=?", did)["title"]:
        raise HTTPException(400, "还没有文案,无法审稿")
    worker.submit_review(did)
    return {"ok": True}


@router.post("/api/drafts/{did}/apply_review")
def apply_review(did: int):
    """采用审稿给出的修改版:替换标题、正文、话题并重新扫描;原审稿结果保留,标记为已采用。"""
    d = _one("SELECT review, image_prompts FROM drafts WHERE id=?", did)
    rv = (d["review"] or {}).get("revised")
    if not rv:
        raise HTTPException(400, "没有可采用的修改版")
    checks = compliance.scan(rv["title"], rv["content"], rv["tags"], d["image_prompts"])
    r = d["review"]; r["applied"] = True
    q_exec("UPDATE drafts SET title=?, content=?, tags=?, checks=?, review=? WHERE id=?", rv["title"], rv["content"],
           json.dumps(rv["tags"], ensure_ascii=False), json.dumps(checks, ensure_ascii=False), json.dumps(r, ensure_ascii=False), did)
    return {"ok": True}


@router.post("/api/drafts/{did}/swap_length")
def swap_length(did: int):
    """在自动压缩版和原稿之间切换(会替换当前正文,没保存的手动修改会丢失)。"""
    d = _one("SELECT title, content, tags, image_prompts, variant FROM drafts WHERE id=?", did)
    try:
        content, v = lengthcap.swap(d["content"], d["variant"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    checks = compliance.scan(d["title"], content, d["tags"], d["image_prompts"])
    q_exec("UPDATE drafts SET content=?, variant=?, checks=? WHERE id=?", content, json.dumps(v, ensure_ascii=False),
           json.dumps(checks, ensure_ascii=False), did)
    return {"ok": True}


@router.post("/api/drafts/{did}/fix")
def fix_draft(did: int):
    _one("SELECT id FROM drafts WHERE id=?", did)
    worker.submit_fix(did)
    return {"ok": True}


@router.post("/api/drafts/{did}/text")
def redo_text(did: int):
    _one("SELECT id FROM drafts WHERE id=?", did)
    worker.submit_text(did)
    return {"ok": True}


@router.post("/api/drafts/{did}/images")
def redo_images(did: int, redo_all: int = Query(0, alias="all")):
    """默认只补缺失的图;?all=1 全部重画。"""
    if not _one("SELECT image_prompts FROM drafts WHERE id=?", did)["image_prompts"]:
        raise HTTPException(400, "没有生图提示词")
    worker.submit_images(did, bool(redo_all))
    return {"ok": True}


# ---------- 发布登记 ----------
class AccountBatch(BaseModel):
    ids: list[int]
    account_id: str = ""   # 空 = 清除


@router.post("/api/drafts/set_account")
def set_account_batch(p: AccountBatch):
    """批量指定(或清除)稿子的发布账号。别人的稿子自动略过。"""
    if not p.ids:
        raise HTTPException(400, "先选要指定账号的稿子")
    aid, name = _pick_account(p.account_id)
    with conn() as c:
        n = sum(c.execute("UPDATE drafts SET account_id=?, account_name=? WHERE id=?", (aid, name, i)).rowcount for i in visible("draft", p.ids[:300]))
    return {"ok": True, "n": n, "account_id": aid, "account_name": name}


class PublishBatch(BaseModel):
    ids: list[int]
    on: bool = True
    published_at: str = ""   # YYYY-MM-DD;空 = 今天


@router.post("/api/drafts/publish")
def publish_batch(p: PublishBatch):
    """批量标记已发布(或取消),可以补填发布日期。发布日期会用来判断数据够不够久。"""
    if not p.ids:
        raise HTTPException(400, "先选要登记的稿子")
    day = p.published_at.strip()
    if day and not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        raise HTTPException(400, "发布日期格式不对,应为 YYYY-MM-DD")
    day = day or time.strftime("%Y-%m-%d")
    with conn() as c:
        ids = visible("draft", p.ids[:200])
        for i in ids:
            c.execute("UPDATE drafts SET published=?, published_at=? WHERE id=?", (int(p.on), day if p.on else "", i))
    return {"ok": True, "n": len(ids)}


@router.get("/api/loop/notes")
def loop_notes(draft_id: int = 0, q: str = ""):
    """手动对号时的候选:千帆里还没对上任何稿子的笔记。给了 draft_id 时,同账号的、标题更像的排前面。"""
    items, _ = qianfan.notes_all()
    taken = {str(v.get("note_id")) for v in qianfan.match_drafts(items).values()}
    if draft_id:
        own("draft", draft_id)
    d = q_one("SELECT title, account_id FROM drafts WHERE id=?", draft_id) if draft_id else None
    key = qianfan._norm_title(q)
    gd = qianfan._grams(qianfan._norm_title(d["title"])) if d else set()
    out = []
    for it in items:
        nid = str(it.get("note_id") or "")
        if not nid or nid in taken or not it.get("title"):
            continue
        t = qianfan._norm_title(it["title"])
        if key and key not in t:
            continue
        g = qianfan._grams(t)
        sim = len(gd & g) / len(gd | g) if gd and g else 0
        same = bool(d and d["account_id"] and it.get("account_id") == d["account_id"])
        out.append({"note_id": nid, "title": it["title"], "published_at": it.get("published_at") or "", "account_name": it.get("account_name") or "",
                    "views": it.get("views"), "likes": it.get("likes"), "collects": it.get("collects"), "comments": it.get("comments"), "sim": round(sim, 2), "same_account": same})
    out.sort(key=lambda x: (x["same_account"], x["sim"], x["published_at"]), reverse=True)
    return {"items": out[:40]}


@router.get("/api/loop/bind_cands")
def bind_cands(ids: str = ""):
    """刚标记已发布的这几篇:每篇给最可能的笔记候选,让人顺手绑定(标记发布时弹出的窗口用)。已经自动对上的标出来,不用再绑。"""
    want = visible("draft", [int(x) for x in ids.split(",") if x.strip().isdigit()][:60])
    if not want:
        return {"items": []}
    items, _ = qianfan.notes_all()
    perf = qianfan.match_drafts(items)
    taken = {str(v.get("note_id")) for v in perf.values()}
    rows = {r["id"]: r for r in autorun._in_chunks("SELECT id, title, account_id, account_name, published_at, images, qf_note_id FROM drafts WHERE id IN ({})", want)}
    out = []
    for i in want:
        r = rows.get(i)
        if not r:
            continue
        hit = perf.get(i)
        out.append({"id": i, "title": r["title"], "account_name": r["account_name"] or "", "published_at": r["published_at"] or "",
                    "cover": next((x for x in (r["images"] or []) if x), ""), "linked": bool(r["qf_note_id"]) and not hit,
                    "matched": ({"title": hit.get("title") or "", "views": hit.get("views")} if hit else None),
                    "cands": [] if hit else qianfan.closest_notes(r["title"], r["account_id"], items, taken, r["published_at"] or "")})
    return {"items": out}


class LinkIn(BaseModel):
    note_id: str = ""
    url: str = ""                    # 小红书笔记链接(里面有 24 位笔记 ID),和 note_id 二选一
    cover: bool | None = None        # 发布时改了封面?(不传 = 不改这一项记录)
    body: bool | None = None         # 发布时改了正文?


@router.post("/api/drafts/{did}/link")
def link_note(did: int, p: LinkIn):
    """手动把稿子对上千帆里的一篇笔记(同时标成已发布,发布日期取笔记的发布日期);note_id 为空 = 取消对应。"""
    d = _one("SELECT id, pub_edit FROM drafts WHERE id=?", did)
    nid = p.note_id.strip()
    if not nid and p.url.strip():
        m = re.search(r"(?<![0-9a-f])[0-9a-f]{24}(?![0-9a-f])", p.url.lower())
        if not m:
            raise HTTPException(400, "链接里没找到笔记 ID。请用笔记页的完整链接(xhslink 开头的短链接,先在浏览器里打开再复制地址栏里的链接)")
        nid = m.group(0)
    if not nid:
        q_exec("UPDATE drafts SET qf_note_id='' WHERE id=?", did)
        return {"ok": True}
    it = next((x for x in qianfan.notes_all()[0] if str(x.get("note_id") or "") == nid), None)
    if not it and not re.fullmatch(r"[0-9a-f]{24}", nid):
        raise HTTPException(400, "千帆数据里没有这篇笔记,可能数据还没同步")
    pe = d["pub_edit"] if isinstance(d["pub_edit"], dict) else {}
    pe = {**pe, "src": "manual", **({"cover": p.cover} if p.cover is not None else {}), **({"body": p.body} if p.body is not None else {})}
    q_exec("UPDATE drafts SET qf_note_id='' WHERE qf_note_id=? AND id!=?", nid, did)   # 一篇笔记只对一篇稿子
    q_exec("UPDATE drafts SET qf_note_id=?, published=1, published_at=COALESCE(NULLIF(?, ''), published_at), pub_edit=? WHERE id=?",
           nid, (it or {}).get("published_at") or "", json.dumps(pe, ensure_ascii=False), did)
    # 笔记不在千帆数据里(刚发、还没同步):先记下 ID,数据同步过来后自动对上
    return {"ok": True, "pending": it is None}


class EditsIn(BaseModel):
    cover: bool | None = None
    body: bool | None = None


@router.post("/api/drafts/{did}/edits")
def set_edits(did: int, p: EditsIn):
    """补记 / 修改「发布时改了封面 / 正文」。"""
    d = _one("SELECT pub_edit FROM drafts WHERE id=?", did)
    pe = d["pub_edit"] if isinstance(d["pub_edit"], dict) else {}
    pe = {**pe, **({"cover": p.cover} if p.cover is not None else {}), **({"body": p.body} if p.body is not None else {})}
    q_exec("UPDATE drafts SET pub_edit=? WHERE id=?", json.dumps(pe, ensure_ascii=False), did)
    return {"ok": True}


# ---------- 爆款回流:复盘 + 继续复刻 ----------
def _my_eval() -> dict:
    """复盘只算自己的稿子(管理员看全部)。"""
    ev = loop.evaluate()
    sid = my_id()
    if sid is not None:
        ev["rows"] = [r for r in ev["rows"] if r.get("owner_id") == sid]
    return ev


@router.get("/api/loop/overview")
def loop_overview():
    return loop.overview(_my_eval())


_TIER_ORDER = {"winner": 0, "good": 1, "normal": 2, "weak": 3, "observing": 4, "nodata": 5, "unpublished": 6}


def _loop_rows(ev: dict, tier: str = "", account_id: str = "") -> list[dict]:
    rows = [r for r in ev["rows"] if (not tier or r["tier"] == tier) and (not account_id or r["account_id"] == account_id)]
    rows.sort(key=lambda r: (_TIER_ORDER[r["tier"]], -(r["lift"] or 0), -r["id"]))
    return rows


@router.get("/api/loop/drafts")
def loop_drafts(tier: str = "", account_id: str = ""):
    ev = _my_eval()
    return {"items": _loop_rows(ev, tier, account_id), "cfg": ev["cfg"], "data_at": ev["data_at"]}


@router.get("/api/loop/page")
def loop_page(tier: str = "", page: int = 1, reg: str = "todo", regpage: int = 1, size: int = 20, wins: int = 12, noacc: int = 0):
    """复盘页一次取齐:只算一次对号,各个列表按页切(原来要请求 5 次,每次都重算,稿子多了很慢)。"""
    size = min(max(size, 1), 100)
    ev = _my_eval()
    rows = _loop_rows(ev, tier)
    regs = _loop_rows(ev, "unpublished" if reg == "todo" else "nodata")
    n_noacc = sum(1 for r in regs if not r["account_id"])   # 没指定发布账号的有几篇(「只看没指定账号」按钮上显示)
    if noacc:
        regs = [r for r in regs if not r["account_id"]]
    page = min(max(page, 1), max(1, -(-len(rows) // size)))
    regpage = min(max(regpage, 1), max(1, -(-len(regs) // size)))
    reg_items = regs[(regpage - 1) * size:regpage * size]
    if reg != "todo" and reg_items:   # 标了发布但没对上:给每篇找一篇最接近的笔记(同账号优先),能一键对上;找不到就说明可能还没发 / 数据没同步
        items_, _ = qianfan.notes_all()
        taken = {str(v.get("note_id")) for v in qianfan.match_drafts(items_).values()}
        reg_items = [{**x, "cands": (cs := qianfan.closest_notes(x["title"], x["account_id"], items_, taken, x.get("published_at") or "")), "closest": cs[0] if cs else None} for x in reg_items]
    if reg_items:   # 发布登记用卡片显示:只给这一页的稿子取封面
        covers = {r["id"]: next((i for i in (r["images"] or []) if i), "") for r in autorun._in_chunks("SELECT id, images FROM drafts WHERE id IN ({})", [x["id"] for x in reg_items])}
        reg_items = [{**x, "cover": covers.get(x["id"], "")} for x in reg_items]
    return {"overview": loop.overview(ev), "winners": _loop_rows(ev, "winner")[:max(wins, 0)],
            "rows": {"items": rows[(page - 1) * size:page * size], "total": len(rows), "page": page},
            "reg": {"items": reg_items, "total": len(regs), "page": regpage, "noacc": n_noacc},
            "size": size, "cfg": ev["cfg"], "data_at": ev["data_at"]}


class SpawnIn(BaseModel):
    count: int = 0


@router.post("/api/drafts/{did}/spawn")
def spawn_draft(did: int, d: SpawnIn):
    """以这篇稿子为样本继续复刻:新建一个任务,走完整的拆解 → 写稿 → 配图 → 质检。"""
    _one("SELECT id FROM drafts WHERE id=?", did)
    if not (get_settings().get("deepseek_api_key") or "").strip():
        raise HTTPException(400, "管理端还没有配置 DeepSeek Key,无法生成")
    try:
        pid = loop.spawn(did, d.count or None, by=(CURRENT.get() or {}).get("username", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": pid}


@router.post("/api/drafts/{did}/replan_images")
def replan_images(did: int):
    """按正文重新策划内页配图并重画(内页会重新扣生图费用)。"""
    _one("SELECT id FROM drafts WHERE id=?", did)
    try:
        worker.replan_images(did)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.delete("/api/drafts/{did}")
def del_draft(did: int):
    q_exec("DELETE FROM drafts WHERE id=?", did)
    return {"ok": True}


# ---------- 导出 / 文件 ----------
class ExportReq(BaseModel):
    ids: list[int]


JOBS: dict[str, dict] = {}   # 导出任务的进度(只在内存里,进程重启就没了;只用来给页面显示进度)


def _export_job(job_id: str, ids: list[int]):
    j = JOBS[job_id]
    try:
        j["zip"] = exporter.export(ids, lambda n, t, stage: j.update(done=n, total=t, stage=stage), owner_id=j["owner_id"])
        j["state"] = "done"
    except Exception as e:
        j["state"], j["error"] = "error", str(e)[:200] or e.__class__.__name__
    j["finished"] = time.time()


@router.post("/api/export/start")
def export_start(req: "ExportReq"):
    """开始导出:立刻返回任务号,在后台整理文件和压缩;页面用 GET /api/export/{job} 查进度。"""
    ids = visible("draft", list(dict.fromkeys(req.ids))[:300])
    if not ids:
        raise HTTPException(400, "请先选择要导出的笔记")
    for k in [k for k, v in JOBS.items() if v.get("finished") and time.time() - v["finished"] > 3600]:
        JOBS.pop(k, None)  # 清掉一小时前的旧任务
    job_id = secrets.token_hex(6)
    JOBS[job_id] = {"state": "running", "done": 0, "total": len(ids), "stage": "copy", "zip": "", "error": "", "started": time.time(), "owner_id": my_id()}
    threading.Thread(target=_export_job, args=(job_id, ids), daemon=True, name=f"export-{job_id}").start()
    return {"job": job_id, "total": len(ids)}


@router.get("/api/export/{job_id}")
def export_status(job_id: str):
    j = JOBS.get(job_id)
    if not j or (my_id() is not None and j["owner_id"] != my_id()):
        raise HTTPException(404, "导出任务不存在(服务重启过?请重新导出)")
    return {k: j[k] for k in ("state", "done", "total", "stage", "zip", "error")}


@router.post("/api/export")
def do_export(req: ExportReq):
    return {"zip": exporter.export(visible("draft", req.ids), owner_id=my_id())}


@router.get("/download/{name}")
def download(name: str):
    p = exporter.EXPORT / Path(name).name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, filename=p.name)


def _img_response(p: Path, w: int, cache: str):
    """w > 0:返回缓存好的缩略图(WebP,浏览器可以长期缓存);否则返回原图。"""
    if w:
        t = thumbs.thumb_for(p, w)
        if t != p:
            return FileResponse(t, media_type="image/webp", headers={"Cache-Control": "private, max-age=31536000, immutable"})
    return FileResponse(p, media_type=mirror.MEDIA.get(p.suffix.lower()), headers={"Cache-Control": cache})


@router.get("/note_images/{name}")
def note_image(name: str, w: int = 0):
    p = mirror.DIR / Path(name).name
    if not p.is_file():
        raise HTTPException(404)
    return _img_response(p, w, "private, max-age=31536000, immutable")  # 文件名是内容哈希,可长期缓存


@router.get("/images/{name}")
def image(name: str, w: int = 0):
    p = imagegen.IMG_DIR / Path(name).name
    if not p.exists():
        raise HTTPException(404)
    return _img_response(p, w, "private, max-age=86400")