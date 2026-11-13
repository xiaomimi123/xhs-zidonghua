"""管理端接口:接口配置、采集监控、笔记库、赛道配置、账号安全。全部要求管理员登录。"""
import json
import re
import secrets
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from .auth import require_admin, is_admin, check_pw, hash_pw, make_token, throttle, SESSION_DAYS, INIT_PW_FILE
from .api_collector import collector_summary
from . import worker
from .db import get_settings, save_settings, SECRET_KEYS, conn, q_all, q_one, q_exec, tier_sql
from .llm import CONTENT_TYPES, ping as ping_deepseek, suggest_topic_merges, expand_keywords, expand_adjacent
from . import imagegen
from .notes import annotate, parse_file, save_notes, localize, unknown_fields
from . import archive, compliance, mirror

public = APIRouter(prefix="/api/admin")
router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


# ---------- 登录 ----------
class PwIn(BaseModel):
    password: str


@public.get("/session")
def session(request: Request):
    return {"ok": is_admin(request)}


@public.post("/login")
def login(d: PwIn, request: Request, response: Response):
    throttle(request, None)
    ok = check_pw(d.password, get_settings()["admin_password_hash"])
    throttle(request, ok)
    if not ok:
        raise HTTPException(400, "密码不正确")
    response.set_cookie("adm", make_token("admin"), max_age=SESSION_DAYS * 86400, httponly=True, samesite="strict")
    return {"ok": True}


@public.post("/logout")
def logout(response: Response):
    response.delete_cookie("adm")
    return {"ok": True}


# ---------- 概览 ----------
@router.get("/overview")
def overview():
    def counts(table):
        return {r["status"]: r["n"] for r in q_all(f"SELECT status, COUNT(*) n FROM {table} GROUP BY status")}
    return {
        "projects": q_one("SELECT COUNT(*) n FROM projects")["n"],
        "notes": q_one("SELECT COUNT(*) n FROM notes")["n"],
        "tiers": {r["k"]: r["n"] for r in q_all(f"SELECT {tier_sql()} k, COUNT(*) n FROM notes n GROUP BY k")},
        "ai": {r["ai_status"]: r["n"] for r in q_all("SELECT ai_status, COUNT(*) n FROM notes GROUP BY ai_status")},
        "mirror": {r["mirror_status"]: r["n"] for r in q_all("SELECT mirror_status, COUNT(*) n FROM notes GROUP BY mirror_status")},
        "mirror_mb": mirror.disk_mb(),
        "tasks": counts("crawl_tasks"), "drafts": counts("drafts"), "playbooks": counts("playbooks"),
        "collector": collector_summary(),
        "init_pw_file": INIT_PW_FILE.exists(),
    }


# ---------- 接口配置 ----------
@router.get("/settings")
def get_conf():
    s = get_settings()
    out = {k: v for k, v in s.items() if k not in SECRET_KEYS}
    out["user_password_set"] = bool(s["user_password_hash"])
    out["staff_count"] = q_one("SELECT COUNT(*) AS n FROM staff")["n"]
    return out


@router.post("/settings")
def set_conf(d: dict):
    save_settings({k: v for k, v in d.items() if k not in SECRET_KEYS})
    if "kol_targets" in d:   # 目标品类改了:达人库的适配度和评分跟着重算
        from . import kol
        kol.rescore()
    worker.wake_tagger()  # 可能刚填了 Key 或打开了自动打标签
    return {"ok": True}


@router.post("/collector_token/reset")
def reset_token():
    t = secrets.token_urlsafe(24)
    save_settings({"collector_token": t})
    return {"token": t}


class ChangePw(BaseModel):
    old: str
    new: str


@router.post("/password")
def change_pw(d: ChangePw):
    if not check_pw(d.old, get_settings()["admin_password_hash"]):
        raise HTTPException(400, "原密码不正确")
    if len(d.new) < 8:
        raise HTTPException(400, "新密码至少 8 位")
    save_settings({"admin_password_hash": hash_pw(d.new)})
    INIT_PW_FILE.unlink(missing_ok=True)
    return {"ok": True}


# ---------- 人员管理 ----------
import re as _re
_USERNAME = _re.compile(r"^[\w.@-]{2,32}$")


class StaffIn(BaseModel):
    username: str
    name: str = ""
    password: str


class StaffPatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None


def _staff_row(r):
    return {k: r[k] for k in ("id", "username", "name", "enabled", "created", "last_login", "last_ip", "projects", "drafts", "live")}


@router.get("/staff")
def staff_list():
    rows = q_all("""SELECT s.*, (SELECT COUNT(*) FROM projects p WHERE p.owner_id=s.id) AS projects,
        (SELECT COUNT(*) FROM drafts d JOIN projects p ON p.id=d.project_id WHERE p.owner_id=s.id AND d.title!='') AS drafts,
        (SELECT COUNT(*) FROM live_jobs j WHERE j.owner_id=s.id) AS live
        FROM staff s ORDER BY s.id""")
    return {"items": [_staff_row(r) for r in rows], "legacy_password": bool(get_settings()["user_password_hash"])}


@router.post("/staff")
def staff_add(d: StaffIn):
    username, name = d.username.strip(), d.name.strip()[:20]
    if not _USERNAME.match(username):
        raise HTTPException(400, "账号 2~32 位,只能用字母、数字、中文和 _ . @ -")
    if len(d.password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    if q_one("SELECT id FROM staff WHERE username=?", username):
        raise HTTPException(400, "这个账号已经存在")
    sid = q_exec("INSERT INTO staff(username,name,password_hash) VALUES(?,?,?)", username, name or username, hash_pw(d.password)).lastrowid
    return {"id": sid}


def _staff_or_404(sid: int):
    s = q_one("SELECT * FROM staff WHERE id=?", sid)
    if not s:
        raise HTTPException(404, "没有这个员工")
    return s


@router.post("/staff/{sid}")
def staff_edit(sid: int, d: StaffPatch):
    s = _staff_or_404(sid)
    if d.name is not None:
        q_exec("UPDATE staff SET name=? WHERE id=?", d.name.strip()[:20] or s["username"], sid)
    if d.enabled is not None:
        # 停用时让已登录的会话立刻失效
        q_exec("UPDATE staff SET enabled=?, token_ver=token_ver+? WHERE id=?", int(d.enabled), 0 if d.enabled else 1, sid)
    return {"ok": True}


class StaffPw(BaseModel):
    password: str


@router.post("/staff/{sid}/password")
def staff_reset_pw(sid: int, d: StaffPw):
    _staff_or_404(sid)
    if len(d.password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    q_exec("UPDATE staff SET password_hash=?, token_ver=token_ver+1 WHERE id=?", hash_pw(d.password), sid)  # 改密码后旧会话失效
    return {"ok": True}


@router.post("/staff/{sid}/kick")
def staff_kick(sid: int):
    _staff_or_404(sid)
    q_exec("UPDATE staff SET token_ver=token_ver+1 WHERE id=?", sid)
    return {"ok": True}


@router.delete("/staff/{sid}")
def staff_delete(sid: int):
    _staff_or_404(sid)
    q_exec("DELETE FROM staff WHERE id=?", sid)  # 他的任务、稿子不删:归属 id 不会再被新账号用到,员工端没人看得到,管理员仍能看到
    return {"ok": True}


@router.post("/user_password")
def set_user_pw(d: PwIn):
    """留空表示取消用户端访问密码。"""
    if d.password and len(d.password) < 6:
        raise HTTPException(400, "访问密码至少 6 位")
    save_settings({"user_password_hash": hash_pw(d.password) if d.password else ""})
    return {"ok": True}


# ---------- 采集监控 ----------
@router.get("/tasks")
def all_tasks(status: str = "", q: str = "", purpose: str = "", limit: int = 200):
    where, args = ["1=1"], []
    if status:
        where.append("t.status=?"); args.append(status)
    if purpose in ("project", "library"):
        where.append("COALESCE(t.purpose,'project')=?"); args.append(purpose)
    if q:
        where.append("(t.query LIKE ? OR p.track LIKE ? OR t.label LIKE ?)"); args += [f"%{q}%"] * 3
    return q_all(f"""SELECT t.*, COALESCE(p.track, t.label) AS track, p.keywords FROM crawl_tasks t LEFT JOIN projects p ON p.id=t.project_id
        WHERE {' AND '.join(where)} ORDER BY t.id DESC LIMIT ?""", *args, min(limit, 1000))


@router.post("/tasks/{tid}/retry")
def retry_task(tid: int):
    q_exec("UPDATE crawl_tasks SET status='pending',error='',worker='',tries=0,claim_start=NULL WHERE id=?", tid)
    return {"ok": True}


@router.post("/tasks/retry_failed")
def retry_failed():
    n = q_exec("UPDATE crawl_tasks SET status='pending',error='',worker='',tries=0,claim_start=NULL WHERE status='failed'").rowcount
    return {"ok": True, "n": n}


@router.delete("/tasks/{tid}")
def del_task(tid: int):
    q_exec("DELETE FROM crawl_tasks WHERE id=?", tid)
    return {"ok": True}


# ---------- 笔记库沉淀任务:采集端空闲时抓取,笔记只进笔记库、不属于任何项目 ----------
class LibTasks(BaseModel):
    type: str = "keyword"
    queries: list[str]
    label: str = ""              # 赛道标签,记在抓到的笔记上
    limit: int = 0               # 每个任务采集条数,0 = 用设置里的默认值
    skip_recent_days: int = 7    # 这么多天内已经采过的同一个词不再重复下发


def _add_library_tasks(kind: str, queries: list[str], label: str, limit: int, skip_days: int) -> dict:
    if kind not in ("keyword", "title", "author"):
        raise HTTPException(400, "任务类型不正确")
    s = get_settings()
    lim = max(1, min(int(limit or s.get("collect_limit") or 20), 100))
    age = 0 if kind == "author" else int(s.get("collect_max_age_days") or 0)
    maxlen = 300 if kind == "author" else 100
    seen, added, skipped = set(), 0, []
    for raw in queries:
        for qy in ([raw] if kind != "keyword" else re.split(r"[,，、;；\n]+", raw)):
            qy = qy.strip()[:maxlen]
            if not qy or qy in seen:
                continue
            seen.add(qy)
            if q_one("SELECT id FROM crawl_tasks WHERE type=? AND query=? AND (status IN ('pending','running') OR "
                     "(status='done' AND finished_at > datetime('now', ?)))", kind, qy, f"-{max(0, skip_days)} days"):
                skipped.append({"query": qy, "reason": "已在队列中或最近采过"})
                continue
            q_exec("INSERT INTO crawl_tasks(project_id,type,query,lim,max_age_days,note_type,sort,purpose,label) VALUES(NULL,?,?,?,?,?,?,'library',?)",
                   kind, qy, lim, age, s.get("collect_note_type") or "image", s.get("collect_sort") or "popularity", label.strip()[:20])
            added += 1
            if added >= 200:
                return {"added": added, "skipped": skipped, "capped": True}
    return {"added": added, "skipped": skipped}


@router.post("/library_tasks")
def add_library_tasks(d: LibTasks):
    if not any(x.strip() for x in d.queries):
        raise HTTPException(400, "请填写要采集的内容")
    return _add_library_tasks(d.type, d.queries, d.label, d.limit, d.skip_recent_days)


class LibFromTracks(BaseModel):
    tracks: list[str]
    limit: int = 0


@router.post("/library_tasks/from_tracks")
def library_from_tracks(d: LibFromTracks):
    """把所选赛道的预设关键词都加入沉淀任务(最近采过的自动跳过)。"""
    total = {"added": 0, "skipped": []}
    for name in d.tracks:
        t = q_one("SELECT keywords FROM tracks WHERE name=?", name)
        if t:
            r = _add_library_tasks("keyword", list(t["keywords"] or []), name, d.limit, 7)
            total["added"] += r["added"]; total["skipped"] += r["skipped"]
    return total


@router.post("/library_tasks/from_projects")
def library_from_projects(d: LibFromTracks):
    """把用户做过的关键词(近 90 天的项目)加入沉淀任务:这些词有真实需求,值得多沉淀一些笔记。"""
    total = {"added": 0, "skipped": []}
    by: dict[str, list[str]] = {}
    for p in q_all("SELECT track, keywords FROM projects WHERE created > datetime('now','-90 days')"):
        by.setdefault(p["track"] or "", []).extend(p["keywords"] or [])
    for track, kws in by.items():
        r = _add_library_tasks("keyword", list(dict.fromkeys(kws)), track, d.limit, 7)
        total["added"] += r["added"]; total["skipped"] += r["skipped"]
    return total


class KwExpand(BaseModel):
    topic: str
    track: str = ""
    n: int = 30
    adjacent: bool = False   # True:生成上位品类、常用菜、同类替代品这类「相邻」的词(品类本身笔记很少时用)


@router.post("/keywords/expand")
def expand_kw(d: KwExpand):
    """给一个产品/品类,用 AI 生成一批关键词(已经在采集队列里或最近采过的会去掉),放进输入框让管理员确认后再添加。"""
    topic = d.topic.strip()
    if not topic:
        raise HTTPException(400, "请填写产品或品类")
    queued = [r["query"] for r in q_all("SELECT DISTINCT query FROM crawl_tasks WHERE type='keyword' AND (status IN ('pending','running') "
                                        "OR (status='done' AND finished_at > datetime('now','-7 days'))) LIMIT 300")]
    try:
        kws = expand_adjacent(topic, d.track, min(d.n, 20), queued) if d.adjacent else expand_keywords(topic, d.track, d.n, queued)
    except Exception as e:
        raise HTTPException(502, f"生成失败:{e}")
    return {"keywords": kws, "skipped_existing": len(queued)}


@router.get("/library_tasks/candidates")
def library_candidates(source: str = "projects", track: str = ""):
    """载入候选关键词到输入框,由管理员删改确认后再添加:source=track(某个赛道的预设词)/ projects(近 90 天用户做过的词)。
    已经在队列里或最近 7 天采过的会去掉。"""
    queued = {r["query"] for r in q_all("SELECT DISTINCT query FROM crawl_tasks WHERE type='keyword' AND (status IN ('pending','running') "
                                        "OR (status='done' AND finished_at > datetime('now','-7 days')))")}
    if source == "track":
        t = q_one("SELECT keywords FROM tracks WHERE name=?", track)
        if not t:
            raise HTTPException(400, "请选择赛道")
        words = list(t["keywords"] or [])
    else:
        words = [k for p in q_all("SELECT keywords FROM projects WHERE created > datetime('now','-90 days')") for k in (p["keywords"] or [])]
    words = list(dict.fromkeys(w.strip() for w in words if w.strip()))
    kws = [w for w in words if w not in queued]
    return {"keywords": kws, "skipped": len(words) - len(kws)}


@router.get("/library_stats")
def library_stats():
    c = {r["status"]: r["n"] for r in q_all("SELECT status, COUNT(*) n FROM crawl_tasks WHERE purpose='library' GROUP BY status")}
    return {"enabled": get_settings().get("library_collect_enabled") != "0", "pending": c.get("pending", 0), "running": c.get("running", 0),
            "done": c.get("done", 0), "failed": c.get("failed", 0),
            "new_total": q_one("SELECT COALESCE(SUM(new_count),0) n FROM crawl_tasks WHERE purpose='library'")["n"],
            "new_7d": q_one("SELECT COALESCE(SUM(new_count),0) n FROM crawl_tasks WHERE purpose='library' AND finished_at > datetime('now','-7 days')")["n"]}


class LibSwitch(BaseModel):
    enabled: bool


@router.post("/library_collect")
def library_switch(d: LibSwitch):
    save_settings({"library_collect_enabled": "1" if d.enabled else "0"})
    return {"ok": True, "enabled": d.enabled}


@router.delete("/library_tasks/pending")
def clear_library_pending():
    n = q_exec("DELETE FROM crawl_tasks WHERE purpose='library' AND status='pending'").rowcount
    return {"ok": True, "n": n}


# ---------- 笔记库(全局) ----------
@router.get("/projects")
def projects():
    return q_all("SELECT p.id, p.track, p.keywords, (SELECT COUNT(*) FROM project_notes WHERE project_id=p.id) note_count "
                 "FROM projects p ORDER BY id DESC")


def _note_filter(track="", keyword="", ai_type="", tier="", ai_status="", project_id=0, q="", topic="", kind="", kind_sub=""):
    where, args = ["1=1"], []
    for col, v in (("n.track", track), ("n.topic", topic), ("n.keyword", keyword), ("n.ai_type", ai_type), ("n.ai_status", ai_status), ("n.kind", kind), ("n.kind_sub", kind_sub)):
        if v:
            where.append(f"{col}=?"); args.append(v)
    if tier:
        where.append(f"{tier_sql()}=?"); args.append(tier)
    if project_id:
        where.append("n.id IN (SELECT note_id FROM project_notes WHERE project_id=?)"); args.append(project_id)
    if q:
        where.append("(n.title LIKE ? OR n.content LIKE ? OR n.author LIKE ? OR n.ai_tags LIKE ?)"); args += [f"%{q}%"] * 4
    return " AND ".join(where), args


@router.get("/notes")
def all_notes(track: str = "", keyword: str = "", ai_type: str = "", tier: str = "", ai_status: str = "",
              project_id: int = 0, q: str = "", sort: str = "new", offset: int = 0, limit: int = 50, topic: str = "", kind: str = "", kind_sub: str = ""):
    w, args = _note_filter(track, keyword, ai_type, tier, ai_status, project_id, q, topic, kind, kind_sub)
    order = {"new": "n.id DESC", "hot": "(n.likes+n.collects+n.comments) DESC", "updated": "n.updated DESC"}.get(sort, "n.id DESC")
    total = q_one(f"SELECT COUNT(*) n FROM notes n WHERE {w}", *args)["n"]
    items = q_all(f"""SELECT n.*, {tier_sql()} AS tier,
        (SELECT COUNT(*) FROM project_notes WHERE note_id=n.id) AS project_count
        FROM notes n WHERE {w} ORDER BY {order} LIMIT ? OFFSET ?""", *args, min(limit, 200), max(offset, 0))
    age = int(get_settings().get("collect_max_age_days") or 0)
    return {"total": total, "items": [annotate(localize(i), age) for i in items]}


class RekindIn(BaseModel):
    scope: str = "failed"   # failed = 只重试出错的;all = 全部重新分类;其他字符串 = 重新分类指定类型


@router.post("/notes/rekind")
def rekind(p: RekindIn):
    """让笔记重新分「帖子类型」(商业硬文 / 产品软文 / 教程 / 测评 / 其他);后台自动处理。"""
    if p.scope == "all":
        n = q_exec("UPDATE notes SET kind_status='pending'").rowcount
    elif p.scope == "failed":
        n = q_exec("UPDATE notes SET kind_status='pending' WHERE kind_status='error'").rowcount
    else:
        n = q_exec("UPDATE notes SET kind_status='pending' WHERE kind=?", p.scope).rowcount
    worker.wake_tagger()
    return {"ok": True, "n": n}


@router.post("/notes/fill_tracks")
def fill_tracks():
    """给没有赛道的笔记补赛道(后台自动做,这里只是立即唤醒)。"""
    worker.wake_tagger()
    return {"ok": True, "n": q_one("SELECT COUNT(*) n FROM notes WHERE track='' OR track IS NULL")["n"]}


@router.get("/notes/facets")
def note_facets(track: str = ""):
    """筛选项和每项的数量。选了赛道时,关键词只列该赛道下的。"""
    def grp(col, where="1=1", *a):
        return [{"k": r["k"], "n": r["n"]} for r in q_all(
            f"SELECT {col} k, COUNT(*) n FROM notes n WHERE {where} GROUP BY k ORDER BY n DESC", *a)]
    return {
        "tracks": grp("n.track"),
        "no_track": q_one("SELECT COUNT(*) n FROM notes WHERE track='' OR track IS NULL")["n"],
        "topics": grp("n.topic", "n.topic!='' AND n.track=?", track) if track else grp("n.topic", "n.topic!=''"),
        "keywords": grp("n.keyword", "n.track=?", track) if track else grp("n.keyword")[:100],
        "types": grp("n.ai_type", "n.ai_status='done'"),
        "kinds": grp("n.kind", "n.kind_status='done'"),
        "kind_status": grp("n.kind_status"),
        "tiers": grp(tier_sql()),
        "ai": grp("n.ai_status"),
        "mirror": grp("n.mirror_status"),
        "content_types": CONTENT_TYPES,
    }


class IdsIn(BaseModel):
    ids: list[int]


@router.post("/notes/delete")
def del_notes(d: IdsIn):
    with conn() as c:
        c.executemany("DELETE FROM project_notes WHERE note_id=?", [(i,) for i in d.ids])
        c.executemany("DELETE FROM notes WHERE id=?", [(i,) for i in d.ids])
    mirror.gc()
    return {"ok": True}


class RetagIn(BaseModel):
    ids: list[int] = []
    scope: str = ""  # failed:全部失败的 / all:全部重打


@router.post("/notes/retag")
def retag(d: RetagIn):
    if d.scope == "failed":
        n = q_exec("UPDATE notes SET ai_status='pending', ai_error='' WHERE ai_status='error'").rowcount
    elif d.scope == "all":
        n = q_exec("UPDATE notes SET ai_status='pending', ai_error='' WHERE ai_status!='running'").rowcount
    else:
        with conn() as c:
            n = sum(c.execute("UPDATE notes SET ai_status='pending', ai_error='' WHERE id=?", (i,)).rowcount for i in d.ids)
    worker.wake_tagger()
    return {"ok": True, "n": n}


class NoteEdit(BaseModel):
    track: str
    keyword: str
    ai_type: str
    topic: str = ""


@router.put("/notes/{nid}")
def edit_note(nid: int, e: NoteEdit):
    """手动修正分类。改了内容类型就视为人工确认,不会被自动打标签覆盖。"""
    q_exec("UPDATE notes SET track=?, keyword=?, ai_type=?, topic=CASE WHEN ?!='' THEN ? ELSE topic END,"
           " ai_status=CASE WHEN ?!='' THEN 'done' ELSE ai_status END, updated=CURRENT_TIMESTAMP WHERE id=?",
           e.track, e.keyword, e.ai_type, e.topic.strip()[:12], e.topic.strip()[:12], e.ai_type, nid)
    return {"ok": True}


@router.post("/notes/import")
async def import_notes(request: Request, filename: str, track: str = "", keyword: str = "", project_id: int = 0):
    """请求体是文件原始内容;文件名用于判断格式。可只归到赛道,也可同时关联到某个项目。"""
    p = q_one("SELECT * FROM projects WHERE id=?", project_id) if project_id else None
    if project_id and not p:
        raise HTTPException(400, "项目不存在")
    track = track or (p["track"] if p else "")
    if not track:
        raise HTTPException(400, "请选择赛道")
    raw = await request.body()
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(400, "文件不能超过 20MB")
    try:
        notes = parse_file(filename, raw)
    except Exception as e:
        raise HTTPException(400, f"文件解析失败:{e}")
    r = save_notes([n for n in notes if isinstance(n, dict)], project_id=project_id or None,
                   track=track, keyword=keyword.strip(), source="import")
    worker.wake_tagger()
    mirror.wake()
    return {"ok": True, "total": len(notes), **r}


# ---------- 赛道配置 ----------
class TrackIn(BaseModel):
    name: str
    keywords: list[str] = []
    enabled: bool = True
    knowledge: str | None = None  # 不传则不改


@router.get("/tracks")
def list_tracks():
    return q_all("SELECT * FROM tracks ORDER BY sort, id")


@router.post("/tracks")
def add_track(t: TrackIn):
    if not t.name.strip():
        raise HTTPException(400, "赛道名称不能为空")
    try:
        q_exec("INSERT INTO tracks(name,keywords,enabled,sort) VALUES(?,?,?,(SELECT COALESCE(MAX(sort),0)+1 FROM tracks))",
               t.name.strip(), json.dumps([k.strip() for k in t.keywords if k.strip()], ensure_ascii=False), int(t.enabled))
    except Exception:
        raise HTTPException(400, "赛道名称已存在")
    return {"ok": True}


@router.put("/tracks/{tid}")
def edit_track(tid: int, t: TrackIn):
    q_exec("UPDATE tracks SET name=?, keywords=?, enabled=?, knowledge=COALESCE(?, knowledge) WHERE id=?", t.name.strip(),
           json.dumps([k.strip() for k in t.keywords if k.strip()], ensure_ascii=False), int(t.enabled), t.knowledge, tid)
    return {"ok": True}


@router.post("/tracks/order")
def order_tracks(d: IdsIn):
    with conn() as c:
        c.executemany("UPDATE tracks SET sort=? WHERE id=?", list(enumerate(d.ids)))
    return {"ok": True}


@router.delete("/tracks/{tid}")
def del_track(tid: int):
    q_exec("DELETE FROM tracks WHERE id=?", tid)
    return {"ok": True}


# ---------- 合规词库 ----------
class RuleIn(BaseModel):
    category: str
    word: str
    replacement: str = ""
    exclude: str = ""
    note: str = ""
    enabled: bool = True


@router.get("/rules")
def list_rules():
    return {"categories": {k: {"name": v[0], "severity": v[1]} for k, v in compliance.CATEGORIES.items()},
            "rules": q_all("SELECT * FROM rules ORDER BY category, id")}


@router.post("/rules")
def add_rule(r: RuleIn):
    if r.category not in compliance.CATEGORIES or not r.word.strip():
        raise HTTPException(400, "分类或词不正确")
    try:
        q_exec("INSERT INTO rules(category,word,replacement,exclude,note,enabled) VALUES(?,?,?,?,?,?)",
               r.category, r.word.strip(), r.replacement, r.exclude, r.note, int(r.enabled))
    except Exception:
        raise HTTPException(400, "该分类下已有这个词")
    return {"ok": True}


@router.put("/rules/{rid}")
def edit_rule(rid: int, r: RuleIn):
    q_exec("UPDATE rules SET category=?,word=?,replacement=?,exclude=?,note=?,enabled=? WHERE id=?",
           r.category, r.word.strip(), r.replacement, r.exclude, r.note, int(r.enabled), rid)
    return {"ok": True}


@router.delete("/rules/{rid}")
def del_rule(rid: int):
    q_exec("DELETE FROM rules WHERE id=?", rid)
    return {"ok": True}


class ScanIn(BaseModel):
    title: str = ""
    content: str = ""


@router.post("/rules/scan")
def try_scan(d: ScanIn):
    return compliance.scan(d.title, d.content, [])


@router.post("/notes/remirror")
def remirror(d: RetagIn):
    """重新转存图片:不传 ids 就重试全部失败的(包括只转存了一部分的)。"""
    return {"ok": True, "n": mirror.retry(d.ids or None)}


@router.get("/notes/fields")
def extra_fields():
    """采集端传了但工作台没有专门使用的字段(完整内容都保存在每篇笔记的原始数据里)。"""
    return {"fields": unknown_fields()}


@router.get("/notes/{nid}/raw")
def note_raw(nid: int):
    r = q_one("SELECT raw FROM notes WHERE id=?", nid)
    if not r:
        raise HTTPException(404, "笔记不存在")
    try:
        return {"raw": json.loads(r["raw"] or "{}")}
    except ValueError:
        return {"raw": {}}


# ---------- 接口连通测试 ----------
def _tested(d: dict) -> dict:
    """用表单里正在填的值测试(不用先保存);Key 留空则沿用已保存的。"""
    s = get_settings()
    for k, v in (d or {}).items():
        if k in s and k not in SECRET_KEYS and not (k.endswith("_api_key") and not str(v).strip()):
            s[k] = str(v).strip()
    return s


@router.post("/test/deepseek")
def test_deepseek(d: dict):
    return ping_deepseek(_tested(d))


@router.post("/test/image")
def test_image(d: dict):
    return imagegen.check(_tested(d))


@router.post("/test/vision")
def test_vision(d: dict):
    """用一张自带的纯红小图问模型是什么颜色,验证地址、Key、模型名和看图能力(几乎不花钱)。"""
    import struct, tempfile, zlib
    from pathlib import Path
    from . import llm as _llm
    ch = lambda t, b: struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xffffffff)
    raw = b"".join(b"\x00" + b"\xdc\x14\x3c" * 64 for _ in range(64))
    png = b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b"")
    f = Path(tempfile.mkdtemp()) / "t.png"
    f.write_bytes(png)
    s = _tested(d)
    try:
        out = _llm.vision_chat("你是图片识别助手。", "这张纯色图片是什么颜色?一个词回答。", [f], s=s).strip()
    except Exception as e:
        return {"ok": False, "msg": str(e)}
    return {"ok": True, "msg": f"模型「{s.get('vision_model')}」能看图,它说这张红色小图是:{out[:30]}"}


@router.post("/test/image_generate")
def test_image_generate(d: dict):
    try:
        r = imagegen.probe(_tested(d))
        return {"ok": True, "msg": f"生成成功,用时 {r['seconds']} 秒", "url": f"/images/{r['file']}"}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


# ---------- 笔记库备份 ----------
class ArchiveIn(BaseModel):
    ids: list[int] = []        # 不传 = 全部笔记
    with_images: bool = True


@router.post("/archive")
def make_archive(d: ArchiveIn):
    """把笔记(含原始数据、AI 标签、图片)打成压缩包,保存在 data/archive/。"""
    return {"ok": True, **archive.export_notes(d.ids or None, d.with_images)}


@router.get("/archive")
def archives():
    return {"items": archive.list_archives()}


@router.get("/archive/{name}")
def download_archive(name: str):
    p = archive.ARCHIVE / Path(name).name
    if not p.is_file() or p.suffix != ".zip":
        raise HTTPException(404, "备份不存在")
    return FileResponse(p, filename=p.name)


# ---------- 主题整理 ----------
class TopicMerge(BaseModel):
    names: list[str]
    to: str


@router.get("/topics")
def topics():
    """所有主题和各有多少篇笔记。"""
    return {"items": q_all("SELECT topic k, COUNT(*) n FROM notes WHERE topic!='' GROUP BY topic ORDER BY n DESC, topic")}


@router.post("/topics/merge")
def merge_topics(d: TopicMerge):
    """把几个相近的主题合并成一个(只勾一个就是改名)。"""
    to = d.to.strip()[:12]
    names = [n for n in dict.fromkeys(x.strip() for x in d.names) if n]
    if not to or not names:
        raise HTTPException(400, "请选择要合并的主题,并填写合并后的名称")
    with conn() as c:
        n = c.execute(f"UPDATE notes SET topic=?, updated=CURRENT_TIMESTAMP WHERE topic IN ({','.join('?' * len(names))})", [to, *names]).rowcount
    return {"ok": True, "n": n, "to": to}


@router.post("/topics/suggest")
def suggest_merges():
    """让 AI 找出该合并的主题。只给方案,不改数据,由管理员确认后再合并。"""
    items = q_all("SELECT topic k, COUNT(*) n FROM notes WHERE topic!='' AND topic!='未识别' GROUP BY topic ORDER BY n DESC, topic")
    for it in items:
        it["samples"] = [r["title"][:16] for r in q_all("SELECT title FROM notes WHERE topic=? ORDER BY likes DESC LIMIT 2", it["k"])]
    try:
        groups = suggest_topic_merges(items)
    except Exception as e:
        raise HTTPException(502, str(e))
    counts = {i["k"]: i["n"] for i in items}
    return {"groups": [{**g, "n": sum(counts.get(x, 0) for x in [*g["from"], g["to"]])} for g in groups]}
