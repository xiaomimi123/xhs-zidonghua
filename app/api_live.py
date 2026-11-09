"""Live 图(iPhone 实况照片):用户端选图下单,Mac 端领任务生成,成品回传到这里下载。

服务端不处理图片:推近动画和 .pvt 打包都要在 Mac 上做(见 tools/livephoto/mac_worker.py、docs/Live图_Mac端说明.md)。
Windows 没有可用的导入路径,所以这里只管任务队列、状态和成品文件。"""
import io
import json
import time
import zipfile
from pathlib import Path
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from . import imagegen
from .auth import CURRENT, require_collector, require_user
from .db import DATA, conn, q_all, q_exec, q_one

router = APIRouter(prefix="/api/live", dependencies=[Depends(require_user)])
worker_router = APIRouter(prefix="/api/live/worker", dependencies=[Depends(require_collector)])

LIVE_DIR = DATA / "live"          # 成品:{任务 id}.zip(里面是 .pvt 包的内容:JPG + MOV + metadata.plist)
STALE_MIN = 20                    # 领走后超过这么多分钟没回传,退回待领取(Mac 合盖休眠时不会卡死)
ONLINE_SEC = 60                   # 这么多秒内有心跳算在线(Mac 端空闲时每 5 秒问一次)
MAX_RESULT = 80 * 1024 * 1024
MAX_BATCH = 100

# 动画参数:服务端只做取值范围限制,具体怎么渲染由 Mac 端决定
DEFAULT_OPTS = {"seconds": 5.0, "zoom": 1.08, "focus_y": 0.35, "fit": "crop"}
FITS = ("crop", "blur")


def clean_opts(o: dict | None) -> dict:
    o = o or {}

    def num(k, lo, hi):
        try:
            return round(min(max(float(o.get(k, DEFAULT_OPTS[k])), lo), hi), 3)
        except (TypeError, ValueError):
            return DEFAULT_OPTS[k]
    fit = o.get("fit") if o.get("fit") in FITS else DEFAULT_OPTS["fit"]
    return {"seconds": num("seconds", 2, 6), "zoom": num("zoom", 1.0, 1.3), "focus_y": num("focus_y", 0, 1), "fit": fit}


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


def _requeue_stale(c):
    c.execute(f"UPDATE live_jobs SET status='pending', worker='' WHERE status='running' AND claimed_at < datetime('now','-{STALE_MIN} minutes')")


# ---------- 用户端 ----------
@router.get("/status")
def status():
    """Mac 端是否在线,以及各状态的任务数。"""
    now = time.time()
    ws = [{"worker": r["worker"], "ip": r["ip"], "ago": int(now - r["last_seen"]), "done": r["done"]}
          for r in q_all("SELECT * FROM live_workers ORDER BY last_seen DESC")]
    counts = {r["status"]: r["n"] for r in q_all("SELECT status, COUNT(*) n FROM live_jobs GROUP BY status")}
    return {"online": bool(ws) and ws[0]["ago"] < ONLINE_SEC, "ago": ws[0]["ago"] if ws else None, "workers": ws,
            "pending": counts.get("pending", 0), "running": counts.get("running", 0), "done": counts.get("done", 0), "error": counts.get("error", 0)}


@router.get("/jobs")
def list_jobs(limit: int = Query(200, le=500)):
    with conn() as c:
        _requeue_stale(c)
    rows = q_all("SELECT j.*, d.title FROM live_jobs j LEFT JOIN drafts d ON d.id=j.draft_id ORDER BY j.id DESC LIMIT ?", limit)
    return [{"id": j["id"], "draft_id": j["draft_id"], "title": j["title"] or f"#{j['draft_id']}", "image": j["image"], "opts": j["opts"],
             "status": j["status"], "error": j["error"], "worker": j["worker"], "created": j["created"], "finished_at": j["finished_at"] or "",
             "size": j["result_size"] or 0, "has_result": j["status"] == "done" and _result_path(j["id"]).is_file(), "by": j["created_by"]} for j in rows]


@router.post("/jobs")
def create_jobs(p: JobsIn):
    """选好的图下单。同一张图、同样的参数已经有排队中 / 生成中 / 已完成的任务就不重复建(想重做用「重新生成」)。"""
    if not p.items:
        raise HTTPException(400, "先选要做成 Live 图的图片")
    if len(p.items) > MAX_BATCH:
        raise HTTPException(400, f"一次最多 {MAX_BATCH} 张")
    opts = clean_opts(p.opts)
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
        ids.append(q_exec("INSERT INTO live_jobs(draft_id,image,opts,created_by) VALUES(?,?,?,?)", it.draft_id, name, ojs, who).lastrowid)
        made += 1
    return {"created": made, "skipped": skipped, "ids": ids}


@router.post("/jobs/{jid}/retry")
def retry(jid: int):
    j = _job_or_404(jid)
    if j["status"] == "running":
        raise HTTPException(400, "正在生成中")
    _result_path(jid).unlink(missing_ok=True)
    q_exec("UPDATE live_jobs SET status='pending', error='', worker='', claimed_at=NULL, finished_at=NULL, result_size=0 WHERE id=?", jid)
    return {"ok": True}


@router.delete("/jobs/{jid}")
def delete(jid: int):
    _job_or_404(jid)
    _result_path(jid).unlink(missing_ok=True)
    q_exec("DELETE FROM live_jobs WHERE id=?", jid)
    return {"ok": True}


@router.post("/jobs/clear_done")
def clear_done():
    """清掉已完成的任务和成品文件(成品传到 iPhone 以后就不需要留着了)。"""
    ids = [r["id"] for r in q_all("SELECT id FROM live_jobs WHERE status='done'")]
    for i in ids:
        _result_path(i).unlink(missing_ok=True)
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


# ---------- Mac 端(采集端 Token 鉴权) ----------
def _beat(request: Request, name: str, done: int = 0):
    w = (name or "mac")[:40]
    q_exec("""INSERT INTO live_workers(worker,ip,last_seen,done) VALUES(?,?,?,?)
        ON CONFLICT(worker) DO UPDATE SET ip=excluded.ip, last_seen=excluded.last_seen, done=done+excluded.done""",
           w, request.client.host if request.client else "", time.time(), done)
    return w


@worker_router.get("/ping")
def w_ping(request: Request, worker: str = ""):
    w = _beat(request, worker)
    n = (q_one("SELECT COUNT(*) n FROM live_jobs WHERE status='pending'") or {"n": 0})["n"]
    return {"ok": True, "worker": w, "pending": n, "server_time": time.strftime("%Y-%m-%d %H:%M:%S")}


@worker_router.get("/claim")
def w_claim(request: Request, worker: str = ""):
    """领一个任务(先进先出)。领走即标记为生成中;超时没回传会自动退回。"""
    w = _beat(request, worker)
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        _requeue_stale(c)
        r = c.execute("SELECT * FROM live_jobs WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
        if not r:
            return {"job": None}
        c.execute("UPDATE live_jobs SET status='running', worker=?, claimed_at=datetime('now'), error='' WHERE id=?", (w, r["id"]))
    return {"job": {"id": r["id"], "image": r["image"], "opts": clean_opts(json.loads(r["opts"] or "{}")),
                    "source": f"/api/live/worker/jobs/{r['id']}/source", "name": _file_stem(r["id"])}}


@worker_router.get("/jobs/{jid}/source")
def w_source(jid: int):
    j = _job_or_404(jid)
    f = imagegen.IMG_DIR / Path(j["image"]).name
    if not f.is_file():
        raise HTTPException(404, "原图已不存在")
    return FileResponse(f)


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
    _beat(request, worker or j["worker"], 1)
    return {"ok": True}


class FailIn(BaseModel):
    error: str = ""


@worker_router.post("/jobs/{jid}/fail")
def w_fail(jid: int, p: FailIn, request: Request, worker: str = ""):
    j = _job_or_404(jid)
    if j["status"] == "running":
        q_exec("UPDATE live_jobs SET status='error', error=?, finished_at=datetime('now') WHERE id=?", p.error[:500] or "生成失败", jid)
    _beat(request, worker or j["worker"])
    return {"ok": True}
