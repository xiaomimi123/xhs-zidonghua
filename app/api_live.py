"""Live 图(iPhone 实况照片):用户端选图下单,后台机器生成,成品回传到这里下载。

两种效果:
  - zoom   慢推镜头:Mac 端直接把图做成推近动画,再打成 .pvt(不需要显卡)
  - motion 真实动作:先由装了 ComfyUI + MiniMax H3 的电脑(视频 worker)把静图生成 3 秒左右的手机随手拍视频,
           再由 Mac 端以这条视频为源做成实况 .pvt

服务端不处理图片和视频,只管队列、状态和文件。Windows 没有可用的实况导入路径,所以最后一步必须在 Mac 上做
(见 tools/livephoto/mac_worker.py、tools/livephoto/video_worker.py、docs/Live图_Mac端说明.md)。"""
import hashlib
import io
import json
import random
import threading
import time
import zipfile
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from . import imagegen, mirror, products
from .auth import CURRENT, require_collector, require_user
from .scope import check as own, my_id, owner_sql
from .db import DATA, conn, get_settings, q_all, q_exec, q_one

router = APIRouter(prefix="/api/live", dependencies=[Depends(require_user)])
worker_router = APIRouter(prefix="/api/live/worker", dependencies=[Depends(require_collector)])    # Mac 端:做实况
vworker_router = APIRouter(prefix="/api/live/vworker", dependencies=[Depends(require_collector)])  # 视频 worker:生成视频

LIVE_DIR = DATA / "live"          # {任务 id}.zip = 成品(.pvt 包内容);{任务 id}.mp4 = 真实动作模式生成的视频
STALE_MIN = 20                    # 领走后超过这么多分钟没回传,退回待领取(机器休眠 / 掉线时不会卡死)
ONLINE_SEC = 60                   # 这么多秒内有心跳算在线(空闲时每 5 秒问一次)
MAX_RESULT = 80 * 1024 * 1024
MAX_VIDEO = 60 * 1024 * 1024
MAX_BATCH = 100

# 动画参数:服务端只做取值范围限制,具体怎么渲染由 worker 决定
DEFAULT_OPTS = {"mode": "zoom", "seconds": 5.0, "zoom": 1.08, "focus_y": 0.35, "fit": "crop", "motion": ""}
FITS = ("crop", "blur")
MODES = ("zoom", "motion")
MOTION_SECONDS = 3.0
MAX_TAKES = 4   # 真实动作:同一张图最多拍几条(每条随机种子不同,挑效果最好的)
# 真实动作模板:提示词统一往「手机随手拍的家庭视频」靠,避免电影感 / AI 感
_BASE = "Casual handheld iPhone video filmed at home, ordinary phone footage, auto-exposure, no cinematic look, no new objects, no text changes"
MOTIONS = {
    "sway": {"label": "手持轻晃", "desc": "像手机随手拍一样轻轻晃动,前后景有细微错位,画面里的东西基本不动,最稳",
             "prompt": f"{_BASE}. The phone sways very gently and drifts slightly, tiny natural hand tremor, subtle parallax between the "
                       "foreground and the background, everything in the scene stays still and keeps its exact look."},
    "rotate": {"label": "手转产品", "desc": "手慢慢转动产品,画面里有手和产品时效果最好;包装文字转动时可能走样",
               "prompt": f"{_BASE}. A hand slowly and slightly rotates the product toward the camera, small natural hand tremor, the background "
                         "stays still and a little out of focus, the product label stays sharp and unchanged."},
    "push": {"label": "镜头靠近", "desc": "手机慢慢靠近主体,带轻微手抖和对焦变化",
             "prompt": f"{_BASE}. The phone slowly moves closer to the main subject and keeps it centered, small natural hand shake, "
                       "gentle focus adjustment, the scene itself stays still."},
}


def clean_opts(o: dict | None) -> dict:
    o = o or {}

    def num(k, lo, hi):
        try:
            return round(min(max(float(o.get(k, DEFAULT_OPTS[k])), lo), hi), 3)
        except (TypeError, ValueError):
            return DEFAULT_OPTS[k]
    mode = o.get("mode") if o.get("mode") in MODES else "zoom"
    fit = o.get("fit") if o.get("fit") in FITS else DEFAULT_OPTS["fit"]
    out = {"mode": mode, "seconds": num("seconds", 2, 6), "zoom": num("zoom", 1.0, 1.3), "focus_y": num("focus_y", 0, 1), "fit": fit, "motion": ""}
    if mode == "motion":
        out["motion"] = o.get("motion") if o.get("motion") in MOTIONS else "sway"
        try:
            t = int(o.get("take") or 0)
        except (TypeError, ValueError):
            t = 0
        if 1 <= t <= MAX_TAKES:
            out["take"] = t   # 第几条:只用来让同一张图的几条任务互不重复,视频 worker 每次都用随机种子
        out["seconds"] = MOTION_SECONDS   # H3 按固定时长生成,用户端不开放
        out["zoom"] = DEFAULT_OPTS["zoom"]
    return out


class JobItem(BaseModel):
    draft_id: int
    image: str


class JobsIn(BaseModel):
    items: list[JobItem]
    opts: dict | None = None
    takes: int = 1                 # 真实动作:每张图拍几条(1~4)


def _take_opts(opts: dict, takes: int) -> list[dict]:
    """真实动作模式下,每张图拍 takes 条:每条的参数里带上「第几条」,互不算重复。慢推镜头没有随机性,只做 1 条。"""
    takes = min(max(int(takes or 1), 1), MAX_TAKES)
    if opts["mode"] != "motion" or takes == 1:
        return [opts]
    return [dict(opts, take=k) for k in range(1, takes + 1)]


def _job_or_404(jid: int) -> dict:
    j = q_one("SELECT * FROM live_jobs WHERE id=?", jid)
    if not j:
        raise HTTPException(404, "任务不存在")
    return j


def _result_path(jid: int) -> Path:
    return LIVE_DIR / f"{int(jid)}.zip"


def _video_path(jid: int) -> Path:
    return LIVE_DIR / f"{int(jid)}.mp4"


def _rm_files(jid: int):
    _result_path(jid).unlink(missing_ok=True)
    _video_path(jid).unlink(missing_ok=True)


def _requeue_stale(c):
    c.execute(f"UPDATE live_jobs SET status='pending', worker='' WHERE status='running' AND claimed_at < datetime('now','-{STALE_MIN} minutes')")


def _workers(table: str) -> dict:
    """后台机器的在线状态。机器忙着做一条任务(视频约 85 秒、实况几十秒)时不发心跳,所以「有任务正在它手上做」也算在线,
    不然每次做视频都会显示离线。"""
    now = time.time()
    busy = {r["worker"] for r in q_all(f"SELECT DISTINCT worker FROM live_jobs WHERE status='running' AND worker!='' AND claimed_at > datetime('now','-10 minutes')")}
    ws = [{"worker": r["worker"], "ip": r["ip"], "ago": int(now - r["last_seen"]), "done": r["done"], "busy": r["worker"] in busy}
          for r in q_all(f"SELECT * FROM {table} ORDER BY last_seen DESC")]
    return {"online": any(w["ago"] < ONLINE_SEC or w["busy"] for w in ws), "ago": ws[0]["ago"] if ws else None, "workers": ws}


# ---------- 用户端 ----------
@router.get("/motions")
def motions():
    """真实动作模板:界面里的下拉选项。"""
    return [{"key": k, "label": v["label"], "desc": v["desc"]} for k, v in MOTIONS.items()]


@router.get("/status")
def status():
    """Mac 端 / 视频 worker 是否在线,以及各状态的任务数。"""
    counts = {(r["status"], r["stage"]): r["n"] for r in q_all("SELECT status, stage, COUNT(*) n FROM live_jobs GROUP BY status, stage")}
    n = lambda st, sg=None: sum(v for (s_, g_), v in counts.items() if s_ == st and (sg is None or g_ == sg))
    mac, vid = _workers("live_workers"), _workers("live_vworkers")
    return {**mac, "pending": n("pending", "live"), "running": n("running", "live"), "done": n("done"), "error": n("error"),
            "video": {**vid, "pending": n("pending", "video"), "running": n("running", "video"),
                      "mask_pending": n("pending", "mask"), "mask_running": n("running", "mask")},
            "image": {"pending": n("pending", "image") + n("pending", "bg"), "running": n("running", "image") + n("running", "bg")}}


_JOB_STATUS = {"run": "j.status IN ('pending','running')", "done": "j.status='done'", "err": "j.status='error'"}
_WHO = "COALESCE(NULLIF(j.created_by,''), '管理员')"


@router.get("/jobs")
def list_jobs(limit: int = Query(200, le=500), offset: int = Query(0, ge=0), status: str = "", by: str = ""):
    """任务列表,按编号从新到旧。status = run / done / err 在服务端筛选(排队的任务很多时,不能只取最新的 N 个再在页面上筛:
    已完成的会被排队中的挤出去);by = 发起人。"""
    with conn() as c:
        _requeue_stale(c)
    w, a = owner_sql("j")
    if status in _JOB_STATUS:
        w += " AND " + _JOB_STATUS[status]
    if by:
        w += f" AND {_WHO}=?"; a = [*a, by]
    rows = q_all("SELECT j.*, COALESCE(NULLIF(j.title,''), d.title) AS shown FROM live_jobs j LEFT JOIN drafts d ON d.id=j.draft_id WHERE 1=1" + w + " ORDER BY j.id DESC LIMIT ? OFFSET ?", *a, limit, offset)
    return [{"id": j["id"], "draft_id": j["draft_id"], "title": j["shown"] or f"#{j['id']}", "image": j["image"], "opts": j["opts"],
             "thumb": j["image"] or (j["gen"] or {}).get("orig", ""), "scene": (j["gen"] or {}).get("scene", "") if j["src"] == "bgswap" else "",
             "src": j["src"], "status": j["status"], "stage": j["stage"], "mode": j["mode"], "error": j["error"], "worker": j["worker"], "created": j["created"],
             "finished_at": j["finished_at"] or "", "size": j["result_size"] or 0, "has_video": _video_path(j["id"]).is_file(),
             "motion": MOTIONS.get((j["opts"] or {}).get("motion"), {}).get("label", ""),
             "has_result": j["status"] == "done" and _result_path(j["id"]).is_file(), "by": j["created_by"]} for j in rows]


@router.get("/jobs/summary")
def jobs_summary(by: str = ""):
    """任务页顶部的数字:各状态各有多少(按发起人筛选时只算这个人的),以及有哪些发起人(不受筛选影响,筛选按钮要一直在)。"""
    w, a = owner_sql("j")
    who = {r["w"]: r["n"] for r in q_all(f"SELECT {_WHO} AS w, COUNT(*) n FROM live_jobs j WHERE 1=1" + w + " GROUP BY w ORDER BY n DESC", *a)}
    w2, a2 = (w + f" AND {_WHO}=?", [*a, by]) if by else (w, a)
    c = {r["s"]: r["n"] for r in q_all("SELECT j.status AS s, COUNT(*) n FROM live_jobs j WHERE 1=1" + w2 + " GROUP BY j.status", *a2)}
    return {"all": sum(c.values()), "run": c.get("pending", 0) + c.get("running", 0), "done": c.get("done", 0), "err": c.get("error", 0), "who": who}


@router.post("/jobs")
def create_jobs(p: JobsIn):
    """选好的图下单。同一张图、同样的参数已经有排队中 / 生成中 / 已完成的任务就不重复建(想重做用「重试」或先删除)。"""
    if not p.items:
        raise HTTPException(400, "先选要做成 Live 图的图片")
    if len(p.items) > MAX_BATCH:
        raise HTTPException(400, f"一次最多 {MAX_BATCH} 张")
    opts = clean_opts(p.opts)
    stage = "video" if opts["mode"] == "motion" else "live"
    who = (CURRENT.get() or {}).get("name", "")
    made, skipped, ids = 0, 0, []
    for it in p.items:
        own("draft", it.draft_id)
        d = q_one("SELECT id, images FROM drafts WHERE id=?", it.draft_id)
        if not d:
            raise HTTPException(404, f"稿子 #{it.draft_id} 不存在")
        name = Path(it.image).name
        if name not in [Path(x).name for x in d["images"] if x]:   # 只允许这篇稿子自己的配图,不能借这个接口读别的文件
            raise HTTPException(400, f"稿子 #{it.draft_id} 没有这张图")
        if not (imagegen.IMG_DIR / name).is_file():
            raise HTTPException(400, f"图片文件不存在:{name}")
        for o in _take_opts(opts, p.takes):
            ojs = json.dumps(o, sort_keys=True)
            dup = q_one("SELECT id FROM live_jobs WHERE draft_id=? AND image=? AND opts=? AND status IN ('pending','running','done')",
                        it.draft_id, name, ojs)
            if dup:
                skipped += 1
                ids.append(dup["id"])
                continue
            ids.append(q_exec("INSERT INTO live_jobs(draft_id,image,opts,created_by,mode,stage,owner_id) VALUES(?,?,?,?,?,?,?)",
                              it.draft_id, name, ojs, who, opts["mode"], stage, my_id()).lastrowid)
            made += 1
    return {"created": made, "skipped": skipped, "ids": ids}


@router.post("/jobs/{jid}/retry")
def retry(jid: int, video: int = 0):
    """重试。真实动作任务:视频已经生成过就只重做实况(省显卡时间);video=1 强制重新生成视频。"""
    j = _job_or_404(jid)
    if j["status"] == "running":
        raise HTTPException(400, "正在生成中")
    stage = "live"
    if j["src"] == "gen" and not j["image"]:
        stage = "image"
    elif j["src"] == "bgswap" and not j["image"]:
        stage = "mask" if (j["gen"] or {}).get("edit") else "bg"
    elif j["mode"] == "motion" and (video or not _video_path(jid).is_file()):
        stage = "video"
        _video_path(jid).unlink(missing_ok=True)
    _result_path(jid).unlink(missing_ok=True)
    q_exec("UPDATE live_jobs SET status='pending', stage=?, error='', worker='', claimed_at=NULL, finished_at=NULL, result_size=0 WHERE id=?", stage, jid)
    return {"ok": True, "stage": stage}


@router.delete("/jobs/{jid}")
def delete(jid: int):
    j = _job_or_404(jid)
    _rm_files(jid)
    q_exec("DELETE FROM live_jobs WHERE id=?", jid)
    g = j["gen"] or {}
    for n in (j["image"], g.get("orig", ""), g.get("edit", "")):
        _gc_image(n)
    return {"ok": True}


@router.post("/jobs/clear_done")
def clear_done():
    """清掉已完成的任务和成品文件(成品传到 iPhone 以后就不需要留着了)。"""
    w, a = owner_sql("live_jobs")
    rows = q_all("SELECT id, image, gen FROM live_jobs WHERE status='done'" + w, *a)
    for r in rows:
        _rm_files(r["id"])
        q_exec("DELETE FROM live_jobs WHERE id=?", r["id"])
    for r in rows:
        g = r["gen"] or {}
        for n in (r["image"], g.get("orig", ""), g.get("edit", "")):
            _gc_image(n)
    return {"deleted": len(rows)}


def _file_stem(jid: int) -> str:
    j = q_one("SELECT j.id, COALESCE(NULLIF(j.title,''), d.title) AS shown FROM live_jobs j LEFT JOIN drafts d ON d.id=j.draft_id WHERE j.id=?", jid) or {}
    t = "".join(ch for ch in (j.get("shown") or "") if ch not in '\\/:*?"<>|\r\n\t ')[:24]
    return f"live_{jid}_{t}" if t else f"live_{jid}"


@router.get("/jobs/{jid}/download")
def download(jid: int):
    """成品包:解压后得到 .pvt 文件夹,双击导入「照片」,再 AirDrop 到 iPhone。"""
    _job_or_404(jid)
    f = _result_path(jid)
    if not f.is_file():
        raise HTTPException(404, "成品还没有生成")
    return FileResponse(f, media_type="application/zip", filename=f"{_file_stem(jid)}.pvt.zip")


@router.get("/jobs/{jid}/preview")
def preview(jid: int):
    """预览:从成品包里取出 MOV 直接播放。"""
    _job_or_404(jid)
    f = _result_path(jid)
    if not f.is_file():
        raise HTTPException(404, "成品还没有生成")
    with zipfile.ZipFile(f) as z:
        mov = next((n for n in z.namelist() if n.lower().endswith(".mov")), None)
        if not mov:
            raise HTTPException(404, "成品包里没有视频")
        data = z.read(mov)
    return Response(data, media_type="video/quicktime", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/jobs/{jid}/video")
def preview_video(jid: int):
    """真实动作模式:视频生成好之后、Mac 还没做完实况之前,可以先看视频效果。"""
    _job_or_404(jid)
    f = _video_path(jid)
    if not f.is_file():
        raise HTTPException(404, "视频还没有生成")
    return FileResponse(f, media_type="video/mp4", headers={"Cache-Control": "private, max-age=3600"})


# ---------- 上传图片 / 产品图生成场景图 ----------
UP_PREFIX = ("lvup_", "lvgen_", "lvref_")   # 这三类文件放在 data/images 里,没有任务再用时一起清理


def _gc_image(name: str):
    """上传 / 生成的图片,没有别的任务在用就删掉(成品库的图不动)。"""
    if name and name.startswith(UP_PREFIX) and not q_one("SELECT 1 AS x FROM live_jobs WHERE image=? OR gen LIKE ?", name, f"%{name}%"):
        (imagegen.IMG_DIR / Path(name).name).unlink(missing_ok=True)


def _save_img(b: bytes, ext: str, prefix: str) -> str:
    name = f"{prefix}{hashlib.sha1(b).hexdigest()[:12]}.{ext}"   # 按内容命名:同一张图上传多次只存一份
    f = imagegen.IMG_DIR / name
    if not f.is_file():
        f.write_bytes(b)
    return name


def _decode(urls: list[str]) -> list[tuple[bytes, str]]:
    try:
        return products.decode_images(urls)
    except ValueError as e:
        raise HTTPException(400, str(e))


class UploadIn(BaseModel):
    images: list[str]              # 浏览器压好的 data URL(jpg / png / webp)
    names: list[str] = []          # 原文件名,用来给任务起名
    opts: dict | None = None
    takes: int = 1                 # 真实动作:每张图拍几条(1~4)


@router.post("/jobs/upload")
def create_from_upload(p: UploadIn):
    """直接上传图片做 Live 图:图片原样作为源图,不经过生图模型,不花生图费用。"""
    imgs = _decode(p.images)
    opts = clean_opts(p.opts)
    stage = "video" if opts["mode"] == "motion" else "live"
    who = (CURRENT.get() or {}).get("name", "")
    made, skipped, ids = 0, 0, []
    for i, (b, ext) in enumerate(imgs):
        name = _save_img(b, ext, "lvup_")
        title = (Path(p.names[i]).stem[:30] if i < len(p.names) and p.names[i] else "") or "上传图片"
        for o in _take_opts(opts, p.takes):
            ojs = json.dumps(o, sort_keys=True)
            dup = q_one("SELECT id FROM live_jobs WHERE image=? AND opts=? AND status IN ('pending','running','done') AND owner_id IS ?", name, ojs, my_id())
            if dup:
                skipped += 1
                ids.append(dup["id"])
                continue
            ids.append(q_exec("INSERT INTO live_jobs(draft_id,image,opts,created_by,mode,stage,title,src,owner_id) VALUES(NULL,?,?,?,?,?,?,'upload',?)",
                              name, ojs, who, opts["mode"], stage, title, my_id()).lastrowid)
            made += 1
    return {"created": made, "skipped": skipped, "ids": ids}


KIND_LABELS = {"drink": "冲泡饮品", "snack": "零食点心", "cook": "烹饪调味", "mixed": "冲饮兼入菜", "side": "下饭佐餐", "food": "一般食品"}


@router.get("/scenes")
def scenes(product_id: int = 0):
    """场景下拉的备选项。不带 product_id 是通用的(换背景页用);带了就按这个产品给:AI 为它推荐的专属场景在前,再加上它这个类型适合的模板场景
    (产品还没有拍摄规格时,这里会让 AI 生成一次,要几秒)。"""
    if product_id:
        prod = products.get(product_id)
        if not prod:
            raise HTTPException(404, "找不到这个产品")
        spec = products.get_spec(prod)
        prod = products.get(product_id)
        from . import covers
        return {"scenes": covers.scene_pool("美食", prod), "kind": KIND_LABELS.get(covers.product_kind(prod), ""), "ai": len(spec.get("scenes") or []),
                "size": products.size_text(prod, spec), "size_user": bool((prod.get("size_note") or "").strip()), "pairings": [x["name"] for x in spec.get("pairings") or []],
                "ready": bool(spec)}
    """产品图生成场景图时可选的生活场景(和成品库配图用同一套场景库)。"""
    from . import covers
    return {"scenes": list(dict.fromkeys(covers.BG_SCENES + covers.GENERIC_SCENES))}


class GenIn(BaseModel):
    product_id: int = 0            # 用「产品」里已有的产品图做参考
    images: list[str] = []         # 或者直接上传产品图(data URL)
    scene: str = ""                # 空 = 自动,每张换一个生活场景;否则按这个场景
    note: str = ""                 # 画面补充描述,如「一只手拿着罐子」
    count: int = 1
    opts: dict | None = None


MAX_GEN = 10


@router.post("/jobs/generate")
def create_from_product(p: GenIn):
    """上传产品图 → 服务端用生图模型生成「手机随手拍」风格的场景图(要花生图费用)→ 再做成 Live 图。"""
    if not 1 <= p.count <= MAX_GEN:
        raise HTTPException(400, f"一次生成 1~{MAX_GEN} 张")
    gen = {"scene": p.scene.strip()[:80], "note": p.note.strip()[:100]}
    title = "产品场景图"
    if p.product_id:
        prod = products.get(p.product_id)
        if not prod or not products.paths(prod):
            raise HTTPException(400, "这个产品没有可用的产品图")
        gen["product_id"] = prod["id"]
        title = prod["name"][:30]
    elif p.images:
        gen["refs"] = [_save_img(b, ext, "lvref_") for b, ext in _decode(p.images)[:3]]
    else:
        raise HTTPException(400, "请选一个产品,或者上传产品图")
    opts = clean_opts(p.opts)
    ojs = json.dumps(opts, sort_keys=True)
    who = (CURRENT.get() or {}).get("name", "")
    seed = random.randint(0, 99)
    # 每张图一个(场景, 拍法),避开这个产品以前用过的:同一产品反复生成,画面才不会越来越像
    from . import covers
    prod_ = products.get(gen["product_id"]) if gen.get("product_id") else None
    spec = products.get_spec(prod_) if prod_ else {}   # 产品拍摄规格:类型 / 搭配(带频率) / 不要出现的东西;没有就让 AI 生成一次
    prod_ = products.get(gen["product_id"]) if gen.get("product_id") else None   # 生成规格后重新取,画像里有了规格,场景池按它的类型挑
    scenes = [gen["scene"]] if gen["scene"] else covers.scene_pool("美食", prod_)
    used_looks, used_pairs = _gen_history(gen)
    looks = covers.pick_looks(p.count, scenes, used_looks)
    pairs = covers.pick_pairings(p.count, spec.get("pairings") or [], used_pairs)
    avoid = products.avoid_text(spec)
    size = products.size_text(prod_, spec) if prod_ else ""
    ids = []
    for k in range(p.count):
        g = dict(gen, idx=seed + k * 3, scene=looks[k][0], look=looks[k][1], auto=not gen["scene"])
        if size:
            g["size"] = size
        if pairs[k]:
            g["pairing"] = pairs[k]
        if avoid:
            g["avoid"] = avoid
        ids.append(q_exec("INSERT INTO live_jobs(draft_id,image,opts,created_by,mode,stage,title,src,gen,owner_id) VALUES(NULL,'',?,?,?,'image',?,'gen',?,?)",
                          ojs, who, opts["mode"], title, json.dumps(g, ensure_ascii=False), my_id()).lastrowid)
    return {"created": len(ids), "ids": ids}


def _used_looks(gen: dict) -> dict:
    return _gen_history(gen)[0]


def _gen_history(gen: dict) -> tuple[dict, dict]:
    """这个产品(或这组上传的产品图)以前生成过的:({(场景, 拍法): 次数}, {搭配: 次数}),只看最近 400 张。"""
    if gen.get("product_id"):
        pid = int(gen["product_id"])
        like, like2 = f'%"product_id": {pid},%', f'%"product_id": {pid}}}%'
    elif gen.get("refs"):
        like = like2 = f'%{gen["refs"][0]}%'
    else:
        return {}, {}
    used: dict = {}
    pairs: dict = {}
    for r in q_all("SELECT gen FROM live_jobs WHERE src='gen' AND (gen LIKE ? OR gen LIKE ?) ORDER BY id DESC LIMIT 400", like, like2):
        g = r["gen"] or {}
        if g.get("look"):
            k = (g.get("scene") or "", g["look"])
            used[k] = used.get(k, 0) + 1
        if g.get("pairing"):
            pairs[g["pairing"]] = pairs.get(g["pairing"], 0) + 1
    return used, pairs


def _gen_prompt(g: dict) -> str:
    from . import covers, worker
    scene = g.get("scene") or covers.scene("美食", g.get("idx", 0), 1)
    if g.get("look"):
        # 机位、光线、画面状态:同一场景也要拍出不一样的画面。参考图只用来保持产品外观,摆放和构图要按拍法重新设计
        scene = f"{scene}。拍法:{g['look']}。构图和摆放按这个拍法重新设计,不要照搬参考图里产品的摆放方式"
    if g.get("pairing"):
        scene += f"。画面里的搭配:{g['pairing']}"   # 产品拍摄规格里的真实吃法,按频率轮换(少用的搭配很少出现)
    if g.get("avoid"):
        scene += f"。{g['avoid']}"
    if g.get("size"):   # 产品实际大小:不然模型不知道多大,盒子和罐子的相对大小、产品和手的比例每张都不一样
        scene += f"。产品的真实大小:{g['size']};画面里产品要和手、杯子、碗等周围的东西保持真实比例,不要画得过大或过小"
    subject = g.get("note") or "产品是画面里的主角,摆在或拿在真实生活场景中,像用户自己随手拍的分享照"
    return worker._image_prompt("", subject, True, scene)


def _gen_refs(g: dict) -> list[Path]:
    if g.get("product_id"):
        prod = products.get(g["product_id"])
        return products.paths(prod, max(1, int(get_settings().get("product_max_refs") or 3))) if prod else []
    return [f for f in (imagegen.IMG_DIR / Path(n).name for n in g.get("refs") or []) if f.is_file()]


def process_gen_job(jid: int):
    """生成一张场景图,成功后把任务交给下一阶段(真实动作 → 视频;慢推镜头 → Mac)。失败记原因,可以重试。"""
    j = q_one("SELECT * FROM live_jobs WHERE id=? AND status='running' AND stage IN ('image','bg')", jid)
    if not j:
        return
    try:
        if j["stage"] == "bg":
            return _process_bg(j)
        g = j["gen"] or {}
        refs = _gen_refs(g)
        if not refs:
            raise RuntimeError("产品图不见了,请重新上传")
        name = imagegen.generate_image(_gen_prompt(g), f"lvgen_{jid}_{int(time.time())}", refs=refs)
        nxt = "video" if (j["opts"] or {}).get("mode") == "motion" else "live"
        q_exec("UPDATE live_jobs SET image=?, status='pending', stage=?, worker='', claimed_at=NULL, error='' WHERE id=?", name, nxt, jid)
    except Exception as e:
        q_exec("UPDATE live_jobs SET status='error', error=?, finished_at=datetime('now') WHERE id=?", f"生成场景图失败:{e}"[:480], jid)


def claim_gen() -> int | None:
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        r = c.execute("SELECT id FROM live_jobs WHERE status='pending' AND stage IN ('image','bg') ORDER BY id LIMIT 1").fetchone()
        if not r:
            return None
        c.execute("UPDATE live_jobs SET status='running', worker='server', claimed_at=datetime('now'), error='' WHERE id=?", (r["id"],))
        return r["id"]


def run_gen_once() -> bool:
    """领一个并同步处理(测试和线程都用它)。"""
    jid = claim_gen()
    if jid is None:
        return False
    process_gen_job(jid)
    return True


_gen_threads: set = set()


def _gen_loop():
    while True:
        try:
            limit = max(1, int(get_settings().get("image_concurrency") or 2))
            _gen_threads.difference_update({t for t in _gen_threads if not t.is_alive()})
            while len(_gen_threads) < limit:
                jid = claim_gen()
                if jid is None:
                    break
                t = threading.Thread(target=process_gen_job, args=(jid,), daemon=True)
                _gen_threads.add(t)
                t.start()
        except Exception:
            pass
        time.sleep(3)


def start_gen():
    """服务启动时调用:把上次中断的场景图任务放回队列,再开监督线程。"""
    q_exec("UPDATE live_jobs SET status='pending', worker='' WHERE status='running' AND stage IN ('image','bg')")
    threading.Thread(target=_gen_loop, daemon=True, name="live-gen").start()


# ---------- 换背景(主体不变) ----------
# 流程:上传实拍图 → [bg:服务端让生图模型只改背景] → [mask:视频 worker 用 SAM3 抠出原图的「背景」,其余全部当主体贴回新背景] → 新图 → 真实动作 / 慢推镜头 → Mac
# 为什么要「贴回」:生图模型改背景时会顺手改主体(包装上的字、位置),把原图主体按掩膜贴回去,主体才是一像素不变。
# 为什么抠「背景」而不是列举主体:实测 SAM3 对偏白、斜放的盒子认不全(漏一个盒子,它的字就被 AI 改了);
# 背景(桌面、墙面)大而完整,抠背景再取补集,图里不管有多少件东西、有没有被识别到,都会保留原样。
SUBJECTS = {"hand": "手", "jar": "罐子", "bottle": "瓶子", "box": "盒子", "cup": "杯子", "spoon": "勺子", "bowl": "碗", "plate": "盘子", "bag": "袋子", "food": "食物"}
DEFAULT_SUBJECT = ["hand", "jar", "box", "cup", "spoon"]   # 旧方式(按主体抠):只在请求里明确给了 subject 时使用
BG_ITEMS = {"table": "桌面", "wall": "墙面", "countertop": "台面", "tablecloth": "桌布", "floor": "地面", "window": "窗户", "curtain": "窗帘", "shelf": "架子"}
DEFAULT_BG = ["table", "wall"]
MAX_BG_IMAGES, MAX_BG_SCENES = 3, 4
_RATIOS = {"1:1": 1.0, "3:4": 0.75, "4:3": 4 / 3, "2:3": 2 / 3, "3:2": 1.5, "9:16": 9 / 16, "16:9": 16 / 9}
BG_KEEP = "保持图中所有的手、产品、容器、餐具、食物和液体的位置、大小、形状、颜色以及包装上的文字完全不变,只把背景(桌面、墙面、杂物等)换成:"
BG_STYLE = "。手机随手拍的生活照风格,家里的自然光,不是棚拍广告大片。画面中不要出现任何新增的文字。"


def _img_size(b: bytes) -> tuple[int, int] | None:
    """读 JPEG / PNG 的宽高(服务端没有 Pillow):用来让生图模型按原图比例出图,贴回主体时才对得上位置。"""
    try:
        if b[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")
        if b[:3] == b"\xff\xd8\xff":
            i = 2
            while i + 9 < len(b):
                if b[i] != 0xFF:
                    i += 1
                    continue
                mk = b[i + 1]
                if mk in (0xD8, 0x01) or 0xD0 <= mk <= 0xD7:
                    i += 2
                    continue
                ln = int.from_bytes(b[i + 2:i + 4], "big")
                if 0xC0 <= mk <= 0xCF and mk not in (0xC4, 0xC8, 0xCC):
                    return int.from_bytes(b[i + 7:i + 9], "big"), int.from_bytes(b[i + 5:i + 7], "big")
                i += 2 + ln
    except Exception:
        return None
    return None


def _nearest_ratio(size: tuple[int, int] | None) -> str:
    if not size or not size[1]:
        return "3:4"
    r = size[0] / size[1]
    return min(_RATIOS, key=lambda k: abs(_RATIOS[k] - r))


def clean_subject(subject: list[str] | None, default: list[str] | None = None) -> list[str]:
    """名词(给 SAM3 的英文提示):只收 2~24 个字母,最多 8 个,去重;一个都没有时用 default。"""
    out = []
    for s in subject or []:
        s = " ".join(str(s).strip().lower().split())
        if 2 <= len(s) <= 24 and all(ch.isalpha() or ch == " " for ch in s) and s not in out:
            out.append(s)
    return out[:8] or list(default or DEFAULT_SUBJECT)


class BgIn(BaseModel):
    images: list[str]                    # 实拍图(浏览器压好的 data URL)
    names: list[str] = []
    scenes: list[str] = []               # 新背景(一个场景 = 一张新图);空 = 自动挑一个生活场景
    background: list[str] = []           # 要换掉的背景是什么(英文名词,如 table / wall);空 = 默认(桌面、墙面);图里其余的东西全部保留
    subject: list[str] = []              # 旧方式:明确列出要保留的主体(英文名词);给了它且没给 background 时才用
    opts: dict | None = None
    takes: int = 1


@router.get("/subjects")
def subjects():
    return {"subjects": [{"key": k, "label": v} for k, v in SUBJECTS.items()], "default": DEFAULT_SUBJECT}


@router.get("/bg_options")
def bg_options():
    """「背景是什么」的选项:选中的会被抠出来当作要换掉的部分,图里其余的东西全部保留原样。"""
    return {"items": [{"key": k, "label": v} for k, v in BG_ITEMS.items()], "default": DEFAULT_BG}


@router.post("/jobs/bgswap")
def create_bgswap(p: BgIn):
    """上传实拍图 → 换成其他背景,主体(手、产品、包装文字)保持原样 → 再做成 Live 图。每个背景要花一次生图费用。"""
    imgs = _decode(p.images)
    if len(imgs) > MAX_BG_IMAGES:
        raise HTTPException(400, f"一次最多 {MAX_BG_IMAGES} 张")
    scenes = [s.strip()[:80] for s in p.scenes if s.strip()][:MAX_BG_SCENES] or [""]
    legacy = bool([s for s in p.subject if str(s).strip()]) and not [s for s in p.background if str(s).strip()]
    subject = clean_subject(p.subject) if legacy else []
    background = [] if legacy else clean_subject(p.background, DEFAULT_BG)
    opts = clean_opts(p.opts)
    who = (CURRENT.get() or {}).get("name", "")
    made, skipped, ids = 0, 0, []
    seed = random.randint(0, 99)
    for i, (b, ext) in enumerate(imgs):
        orig = _save_img(b, ext, "lvup_")
        title = (Path(p.names[i]).stem[:24] if i < len(p.names) and p.names[i] else "") or "实拍图"
        ratio = _nearest_ratio(_img_size(b))
        for k, sc in enumerate(scenes):
            for o in _take_opts(opts, p.takes):
                g = {"kind": "bgswap", "orig": orig, "scene": sc, "subject": subject, "background": background, "ratio": ratio, "idx": seed + k * 3 + i}
                ojs = json.dumps(o, sort_keys=True)
                dup = q_one("SELECT id FROM live_jobs WHERE src='bgswap' AND opts=? AND status IN ('pending','running','done') AND owner_id IS ? AND gen LIKE ? AND gen LIKE ? AND gen LIKE ? AND gen LIKE ?",
                            ojs, my_id(), f'%"orig": "{orig}"%', f'%"scene": {json.dumps(sc, ensure_ascii=False)}%',
                            f'%"subject": {json.dumps(subject)}%', f'%"background": {json.dumps(background)}%')
                if dup:
                    skipped += 1
                    ids.append(dup["id"])
                    continue
                ids.append(q_exec("INSERT INTO live_jobs(draft_id,image,opts,created_by,mode,stage,title,src,gen,owner_id) VALUES(NULL,'',?,?,?,'bg',?,'bgswap',?,?)",
                                  ojs, who, o["mode"], f"{title}·换背景", json.dumps(g, ensure_ascii=False), my_id()).lastrowid)
                made += 1
    return {"created": made, "skipped": skipped, "ids": ids}


def _bg_prompt(g: dict) -> str:
    from . import covers
    scene = g.get("scene") or covers.scene("美食", g.get("idx", 0), 1)
    return BG_KEEP + scene + BG_STYLE


def _process_bg(j: dict):
    """让生图模型只改背景(原图当参考,按原图比例出图),交给视频 worker 抠主体、贴回。"""
    g = j["gen"] or {}
    orig = imagegen.IMG_DIR / Path(g.get("orig", "")).name
    if not orig.is_file():
        raise RuntimeError("原图不见了,请重新上传")
    s = {**get_settings(), "image_size": g.get("ratio") or "3:4"}
    name = imagegen.generate_image(_bg_prompt(g), f"lvgen_{j['id']}_{int(time.time())}", s=s, refs=[orig])
    g["edit"] = name
    q_exec("UPDATE live_jobs SET gen=?, status='pending', stage='mask', worker='', claimed_at=NULL, error='' WHERE id=?",
           json.dumps(g, ensure_ascii=False), j["id"])


# ---------- 后台机器的心跳 ----------
def _beat(table: str, request: Request, name: str, done: int = 0):
    w = (name or "worker")[:40]
    q_exec(f"""INSERT INTO {table}(worker,ip,last_seen,done) VALUES(?,?,?,?)
        ON CONFLICT(worker) DO UPDATE SET ip=excluded.ip, last_seen=excluded.last_seen, done=done+excluded.done""",
           w, request.client.host if request.client else "", time.time(), done)
    return w


def _claim(table: str, stage, request: Request, worker: str):
    """stage 可以是一个阶段,也可以是按优先级排列的几个阶段。"""
    w = _beat(table, request, worker)
    stages = (stage,) if isinstance(stage, str) else tuple(stage)
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        _requeue_stale(c)
        r = None
        for sg in stages:
            r = c.execute("SELECT * FROM live_jobs WHERE status='pending' AND stage=? ORDER BY id LIMIT 1", (sg,)).fetchone()
            if r:
                break
        if not r:
            return w, None
        c.execute("UPDATE live_jobs SET status='running', worker=?, claimed_at=datetime('now'), error='' WHERE id=?", (w, r["id"]))
    return w, r


def _fail(table: str, jid: int, error: str, request: Request, worker: str):
    j = _job_or_404(jid)
    if j["status"] == "running":
        q_exec("UPDATE live_jobs SET status='error', error=?, finished_at=datetime('now') WHERE id=?", error[:500] or "生成失败", jid)
    _beat(table, request, worker or j["worker"])


def _source(jid: int):
    j = _job_or_404(jid)
    f = imagegen.IMG_DIR / Path(j["image"]).name
    if not f.is_file():
        raise HTTPException(404, "原图已不存在")
    return FileResponse(f)


class FailIn(BaseModel):
    error: str = ""


# ---------- Mac 端(采集端 Token 鉴权):做实况 ----------
@worker_router.get("/ping")
def w_ping(request: Request, worker: str = ""):
    w = _beat("live_workers", request, worker)
    n = (q_one("SELECT COUNT(*) n FROM live_jobs WHERE status='pending' AND stage='live'") or {"n": 0})["n"]
    return {"ok": True, "worker": w, "pending": n, "server_time": time.strftime("%Y-%m-%d %H:%M:%S")}


@worker_router.get("/claim")
def w_claim(request: Request, worker: str = ""):
    """领一个任务(先进先出)。真实动作模式要等视频生成好才轮到 Mac。领走即标记为生成中;超时没回传会自动退回。"""
    w, r = _claim("live_workers", "live", request, worker)
    if not r:
        return {"job": None}
    opts = clean_opts(json.loads(r["opts"] or "{}"))
    job = {"id": r["id"], "image": r["image"], "opts": opts, "mode": opts["mode"],
           "source": f"/api/live/worker/jobs/{r['id']}/source", "name": _file_stem(r["id"]),
           "by": r["created_by"] or "",   # 发起人,Mac 导相册时按它分相册
           "created": (r["created"] or "")[:10]}   # 任务创建日期(YYYY-MM-DD),Mac 按它分日期子文件夹/相册
    if opts["mode"] == "motion":
        job["video"] = f"/api/live/worker/jobs/{r['id']}/video"
    return {"job": job}


@worker_router.get("/owners")
def w_owners():
    """{任务号: 发起人}:Mac 上的整理脚本据此把已有成品按发起人分文件夹。"""
    return {str(r["id"]): r["created_by"] or "管理员" for r in q_all("SELECT id, created_by FROM live_jobs")}


@worker_router.get("/jobs/{jid}/source")
def w_source(jid: int):
    return _source(jid)


@worker_router.get("/jobs/{jid}/video")
def w_video(jid: int):
    _job_or_404(jid)
    f = _video_path(jid)
    if not f.is_file():
        raise HTTPException(404, "视频不存在")
    return FileResponse(f, media_type="video/mp4")


@worker_router.put("/jobs/{jid}/result")
async def w_result(jid: int, request: Request, worker: str = ""):
    """回传成品:请求体就是 zip(.pvt 包的内容)。"""
    j = _job_or_404(jid)
    if j["status"] != "running":
        raise HTTPException(409, "这个任务不在生成中(可能已被删除或退回重领)")
    body = await request.body()
    if not body or len(body) > MAX_RESULT:
        raise HTTPException(400, "成品文件为空或太大")
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            names = [n.lower() for n in z.namelist()]
    except zipfile.BadZipFile:
        raise HTTPException(400, "成品不是有效的 zip")
    if not any(n.endswith(".mov") for n in names) or not any(n.endswith((".jpg", ".jpeg", ".heic")) for n in names):
        raise HTTPException(400, "成品包里要同时有图片(JPG/HEIC)和视频(MOV)")
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    _result_path(jid).write_bytes(body)
    q_exec("UPDATE live_jobs SET status='done', error='', finished_at=datetime('now'), result_size=? WHERE id=?", len(body), jid)
    _beat("live_workers", request, worker or j["worker"], 1)
    return {"ok": True}


@worker_router.post("/jobs/{jid}/fail")
def w_fail(jid: int, p: FailIn, request: Request, worker: str = ""):
    _fail("live_workers", jid, p.error, request, worker)
    return {"ok": True}


# ---------- 视频 worker(采集端 Token 鉴权):真实动作模式,把静图生成短视频 ----------
@vworker_router.get("/ping")
def v_ping(request: Request, worker: str = ""):
    w = _beat("live_vworkers", request, worker)
    n = (q_one("SELECT COUNT(*) n FROM live_jobs WHERE status='pending' AND stage IN ('video','mask')") or {"n": 0})["n"]
    return {"ok": True, "worker": w, "pending": n, "server_time": time.strftime("%Y-%m-%d %H:%M:%S")}


@vworker_router.get("/claim")
def v_claim(request: Request, worker: str = ""):
    """先领「抠主体 + 贴回新背景」(很快),没有再领「生成视频」。"""
    w, r = _claim("live_vworkers", ("mask", "video"), request, worker)
    if not r:
        return {"job": None}
    opts = clean_opts(json.loads(r["opts"] or "{}"))
    if r["stage"] == "mask":
        g = json.loads(r["gen"] or "{}")
        bgp = clean_subject(g.get("background"), DEFAULT_BG) if g.get("background") or not g.get("subject") else []
        return {"job": {"id": r["id"], "stage": "mask", "opts": opts, "mode": "background" if bgp else "subject",
                        "background": bgp, "subject": [] if bgp else clean_subject(g.get("subject")),
                        "orig": f"/api/live/vworker/jobs/{r['id']}/file/orig", "edit": f"/api/live/vworker/jobs/{r['id']}/file/edit"}}
    m = MOTIONS[opts["motion"] or "sway"]
    return {"job": {"id": r["id"], "stage": "video", "image": r["image"], "opts": opts, "motion": opts["motion"], "prompt": m["prompt"],
                    "seconds": opts["seconds"], "source": f"/api/live/vworker/jobs/{r['id']}/source"}}


@vworker_router.get("/jobs/{jid}/source")
def v_source(jid: int):
    return _source(jid)


@vworker_router.put("/jobs/{jid}/video")
async def v_video(jid: int, request: Request, worker: str = ""):
    """回传生成好的视频(请求体就是 mp4)。收到后任务进入 Mac 的队列。"""
    j = _job_or_404(jid)
    if j["status"] != "running" or j["stage"] != "video":
        raise HTTPException(409, "这个任务不在生成视频阶段(可能已被删除或退回重领)")
    body = await request.body()
    if not body or len(body) > MAX_VIDEO:
        raise HTTPException(400, "视频文件为空或太大")
    if body[4:8] != b"ftyp":
        raise HTTPException(400, "不是有效的 mp4 / mov 视频")
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    _video_path(jid).write_bytes(body)
    q_exec("UPDATE live_jobs SET status='pending', stage='live', worker='', claimed_at=NULL, error='', video_size=? WHERE id=?", len(body), jid)
    _beat("live_vworkers", request, worker or j["worker"], 1)
    return {"ok": True}


@vworker_router.post("/jobs/{jid}/fail")
def v_fail(jid: int, p: FailIn, request: Request, worker: str = ""):
    _fail("live_vworkers", jid, p.error, request, worker)
    return {"ok": True}


@vworker_router.get("/jobs/{jid}/file/{which}")
def v_file(jid: int, which: str):
    """换背景:取原图(orig)或生图模型改过背景的图(edit)。"""
    j = _job_or_404(jid)
    name = (j["gen"] or {}).get("orig" if which == "orig" else "edit" if which == "edit" else "", "")
    f = imagegen.IMG_DIR / Path(name).name if name else None
    if not f or not f.is_file():
        raise HTTPException(404, "图片不存在")
    return FileResponse(f)


@vworker_router.put("/jobs/{jid}/image")
async def v_image(jid: int, request: Request, worker: str = ""):
    """回传合成好的新图(原图主体 + 新背景):收到后接着做 Live(真实动作 → 生成视频;慢推镜头 → Mac)。"""
    j = _job_or_404(jid)
    if j["status"] != "running" or j["stage"] != "mask":
        raise HTTPException(409, "这个任务不在合成阶段(可能已被删除或退回重领)")
    body = await request.body()
    ext = mirror.sniff(body) if body else None
    if not ext or ext not in ("jpg", "png", "webp") or len(body) > MAX_VIDEO:
        raise HTTPException(400, "不是有效的图片")
    name = _save_img(body, ext, "lvgen_")
    nxt = "video" if (j["opts"] or {}).get("mode") == "motion" else "live"
    q_exec("UPDATE live_jobs SET image=?, status='pending', stage=?, worker='', claimed_at=NULL, error='' WHERE id=?", name, nxt, jid)
    _beat("live_vworkers", request, worker or j["worker"], 1)
    return {"ok": True}
