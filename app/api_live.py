"""Live 图(iPhone 实况照片):用户端选图下单,后台机器生成,成品回传到这里下载。

两种效果:
  - zoom   慢推镜头:Mac 端直接把图做成推近动画,再打成 .pvt(不需要显卡)
  - motion 真实动作:先由装了 ComfyUI + MiniMax H3 的电脑(视频 worker)把静图生成 3 秒左右的手机随手拍视频,
           再由 Mac 端以这条视频为源做成实况 .pvt

服务端不处理图片和视频,只管队列、状态和文件。Windows 没有可用的实况导入路径,所以最后一步必须在 Mac 上做
(见 tools/livephoto/mac_worker.py、tools/livephoto/video_worker.py、docs/Live图_Mac端说明.md)。"""
import io
import json
import time
import zipfile
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from . import imagegen
from .auth import CURRENT, require_collector, require_user
from .db import DATA, conn, q_all, q_exec, q_one

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
        out["seconds"] = MOTION_SECONDS   # H3 按固定时长生成,用户端不开放
        out["zoom"] = DEFAULT_OPTS["zoom"]
    return out


class JobItem(BaseModel):
    draft_id: int
    image: str


class JobsIn(BaseModel):
    items: list[JobItem]
    opts: dict | None = None


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
    now = time.time()
    ws = [{"worker": r["worker"], "ip": r["ip"], "ago": int(now - r["last_seen"]), "done": r["done"]}
          for r in q_all(f"SELECT * FROM {table} ORDER BY last_seen DESC")]
    return {"online": bool(ws) and ws[0]["ago"] < ONLINE_SEC, "ago": ws[0]["ago"] if ws else None, "workers": ws}


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
            "video": {**vid, "pending": n("pending", "video"), "running": n("running", "video")}}


@router.get("/jobs")
def list_jobs(limit: int = Query(200, le=500)):
    with conn() as c:
        _requeue_stale(c)
    rows = q_all("SELECT j.*, d.title FROM live_jobs j LEFT JOIN drafts d ON d.id=j.draft_id ORDER BY j.id DESC LIMIT ?", limit)
    return [{"id": j["id"], "draft_id": j["draft_id"], "title": j["title"] or f"#{j['draft_id']}", "image": j["image"], "opts": j["opts"],
             "status": j["status"], "stage": j["stage"], "mode": j["mode"], "error": j["error"], "worker": j["worker"], "created": j["created"],
             "finished_at": j["finished_at"] or "", "size": j["result_size"] or 0, "has_video": _video_path(j["id"]).is_file(),
             "motion": MOTIONS.get((j["opts"] or {}).get("motion"), {}).get("label", ""),
             "has_result": j["status"] == "done" and _result_path(j["id"]).is_file(), "by": j["created_by"]} for j in rows]


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
    ojs = json.dumps(opts, sort_keys=True)
    for it in p.items:
        d = q_one("SELECT id, images FROM drafts WHERE id=?", it.draft_id)
        if not d:
            raise HTTPException(404, f"稿子 #{it.draft_id} 不存在")
        name = Path(it.image).name
        if name not in [Path(x).name for x in d["images"] if x]:   # 只允许这篇稿子自己的配图,不能借这个接口读别的文件
            raise HTTPException(400, f"稿子 #{it.draft_id} 没有这张图")
        if not (imagegen.IMG_DIR / name).is_file():
            raise HTTPException(400, f"图片文件不存在:{name}")
        dup = q_one("SELECT id FROM live_jobs WHERE draft_id=? AND image=? AND opts=? AND status IN ('pending','running','done')",
                    it.draft_id, name, ojs)
        if dup:
            skipped += 1
            ids.append(dup["id"])
            continue
        ids.append(q_exec("INSERT INTO live_jobs(draft_id,image,opts,created_by,mode,stage) VALUES(?,?,?,?,?,?)",
                          it.draft_id, name, ojs, who, opts["mode"], stage).lastrowid)
        made += 1
    return {"created": made, "skipped": skipped, "ids": ids}


@router.post("/jobs/{jid}/retry")
def retry(jid: int, video: int = 0):
    """重试。真实动作任务:视频已经生成过就只重做实况(省显卡时间);video=1 强制重新生成视频。"""
    j = _job_or_404(jid)
    if j["status"] == "running":
        raise HTTPException(400, "正在生成中")
    stage = "live"
    if j["mode"] == "motion" and (video or not _video_path(jid).is_file()):
        stage = "video"
        _video_path(jid).unlink(missing_ok=True)
    _result_path(jid).unlink(missing_ok=True)
    q_exec("UPDATE live_jobs SET status='pending', stage=?, error='', worker='', claimed_at=NULL, finished_at=NULL, result_size=0 WHERE id=?", stage, jid)
    return {"ok": True, "stage": stage}


@router.delete("/jobs/{jid}")
def delete(jid: int):
    _job_or_404(jid)
    _rm_files(jid)
    q_exec("DELETE FROM live_jobs WHERE id=?", jid)
    return {"ok": True}


@router.post("/jobs/clear_done")
def clear_done():
    """清掉已完成的任务和成品文件(成品传到 iPhone 以后就不需要留着了)。"""
    ids = [r["id"] for r in q_all("SELECT id FROM live_jobs WHERE status='done'")]
    for i in ids:
        _rm_files(i)
    q_exec("DELETE FROM live_jobs WHERE status='done'")
    return {"deleted": len(ids)}


def _file_stem(jid: int) -> str:
    j = q_one("SELECT j.id, d.title FROM live_jobs j LEFT JOIN drafts d ON d.id=j.draft_id WHERE j.id=?", jid) or {}
    t = "".join(ch for ch in (j.get("title") or "") if ch not in '\\/:*?"<>|\r\n\t ')[:24]
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


# ---------- 后台机器的心跳 ----------
def _beat(table: str, request: Request, name: str, done: int = 0):
    w = (name or "worker")[:40]
    q_exec(f"""INSERT INTO {table}(worker,ip,last_seen,done) VALUES(?,?,?,?)
        ON CONFLICT(worker) DO UPDATE SET ip=excluded.ip, last_seen=excluded.last_seen, done=done+excluded.done""",
           w, request.client.host if request.client else "", time.time(), done)
    return w


def _claim(table: str, stage: str, request: Request, worker: str):
    w = _beat(table, request, worker)
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        _requeue_stale(c)
        r = c.execute("SELECT * FROM live_jobs WHERE status='pending' AND stage=? ORDER BY id LIMIT 1", (stage,)).fetchone()
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
           "source": f"/api/live/worker/jobs/{r['id']}/source", "name": _file_stem(r["id"])}
    if opts["mode"] == "motion":
        job["video"] = f"/api/live/worker/jobs/{r['id']}/video"
    return {"job": job}


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
    n = (q_one("SELECT COUNT(*) n FROM live_jobs WHERE status='pending' AND stage='video'") or {"n": 0})["n"]
    return {"ok": True, "worker": w, "pending": n, "server_time": time.strftime("%Y-%m-%d %H:%M:%S")}


@vworker_router.get("/claim")
def v_claim(request: Request, worker: str = ""):
    w, r = _claim("live_vworkers", "video", request, worker)
    if not r:
        return {"job": None}
    opts = clean_opts(json.loads(r["opts"] or "{}"))
    m = MOTIONS[opts["motion"] or "sway"]
    return {"job": {"id": r["id"], "image": r["image"], "opts": opts, "motion": opts["motion"], "prompt": m["prompt"],
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
