"""采集端对接接口(标准见 docs/采集端接口.md),全部要求采集端 Token。"""
import json
import time
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from .auth import require_collector
from . import mirror, qianfan, worker
from .db import conn, get_settings, q_all, q_exec, q_one
from .notes import save_notes, split_by_age

router = APIRouter(prefix="/api", dependencies=[Depends(require_collector)])
STALE_MIN = 15  # 领走任务后超过这么多分钟没回传,任务退回待领取
MAX_TRIES = 3   # 超时没回传被退回这么多次还不行,就标失败(采集端领走又丢掉的任务不能无限循环)
LEASE_MIN = {"qianfan": 10}   # 心跳续租最多续到领走后多少分钟(千帆任务几十秒就做完);其他任务 60 分钟
ONLINE_SEC = 150  # 这么多秒内有心跳(ping、领任务、回传笔记)算在线;采集端忙的时候大约每 60 秒才有一次


def collector_summary() -> dict:
    now = time.time()
    ws = [{"worker": r["worker"], "ip": r["ip"], "ago": int(now - r["last_seen"]), "claimed": r["claimed"],
           "first_seen": r["first_seen"], "known_calls": r["known_calls"] or 0, "known_ago": int(now - r["known_at"]) if r["known_at"] else None,
           "known_total": r["known_total"] or 0, "known_hit": r["known_hit"] or 0} for r in q_all("SELECT * FROM collectors ORDER BY last_seen DESC")]
    return {"online": bool(ws) and ws[0]["ago"] < ONLINE_SEC, "online_sec": ONLINE_SEC, "ago": ws[0]["ago"] if ws else None, "workers": ws}


def _resolve_worker(c, name: str = "", task_id: int = 0) -> str:
    """没带采集端名称的请求(回传笔记、查询已有笔记)算到谁头上:先看任务是谁领的,再看最近活跃的采集端。"""
    if name:
        return name
    if task_id:
        r = c.execute("SELECT worker FROM crawl_tasks WHERE id=?", (task_id,)).fetchone()
        if r and r[0]:
            return r[0]
    r = c.execute("SELECT worker FROM collectors WHERE worker!='default' ORDER BY last_seen DESC LIMIT 1").fetchone()
    return r[0] if r else "default"


def _heartbeat(c, request: Request, worker_name: str, claimed: int):
    w = worker_name or "default"
    c.execute("""INSERT INTO collectors(worker,ip,last_seen,claimed) VALUES(?,?,?,?)
        ON CONFLICT(worker) DO UPDATE SET ip=excluded.ip, last_seen=excluded.last_seen, claimed=claimed+excluded.claimed""",
              (w, request.client.host if request.client else "", time.time(), claimed))
    # 心跳同时续租:这个采集端正在采的任务,超时计时从现在重新开始(否则采得慢的任务会在 15 分钟时被退回、被重复领取)。
    # 续租有上限(从第一次领走算起):采集端领走任务后又丢掉了(回传时网络断了、程序出错没报),只要它还在轮询,
    # 无限续租会让这个任务永远停在「运行中」,再也退不回去。
    c.execute("UPDATE crawl_tasks SET claimed_at=datetime('now') WHERE status='running' AND worker=? "
              "AND COALESCE(claim_start, claimed_at) > datetime('now', CASE WHEN purpose='qianfan' THEN ? ELSE ? END)",
              (w, f"-{LEASE_MIN['qianfan']} minutes", "-60 minutes"))


def _expire_stale(c):
    """超时没回传的任务:退回待领取(tries+1);退回 MAX_TRIES 次还没结果就标失败,并让达人库里排队中的联系方式显示出错。"""
    for r in c.execute("SELECT id, query, params, tries FROM crawl_tasks WHERE status='running' AND claimed_at < datetime('now', ?)",
                       (f"-{STALE_MIN} minutes",)).fetchall():
        if (r["tries"] or 0) + 1 >= MAX_TRIES:
            msg = f"采集端领走后一直没有回传结果(已重试 {MAX_TRIES} 次),可以在采集监控里点「重新下发」"
            c.execute("UPDATE crawl_tasks SET status='failed', error=?, finished_at=datetime('now'), tries=COALESCE(tries,0)+1 WHERE id=?", (msg, r["id"]))
            if r["query"] == "buyer_contact":
                try:
                    cid = json.loads(r["params"] or "{}").get("creator_id")
                except Exception:
                    cid = None
                if cid:
                    c.execute("UPDATE kol_creators SET contact_status='error', contact_error=? WHERE creator_id=? AND contact_status='queued'", (msg[:200], cid))
        else:
            c.execute("UPDATE crawl_tasks SET status='pending', worker='', tries=COALESCE(tries,0)+1 WHERE id=?", (r["id"],))


@router.get("/ping")
def ping(request: Request, worker_name: str = Query("", alias="worker")):
    """连通性自检:地址、Token 都对就返回 ok,同时记一次心跳。不领取任何任务。"""
    with conn() as c:
        _heartbeat(c, request, worker_name, 0)
        pending = c.execute("SELECT COUNT(*) FROM crawl_tasks WHERE status='pending'").fetchone()[0]
    return {"ok": True, "server_time": time.strftime("%Y-%m-%d %H:%M:%S"), "worker": worker_name or "default",
            "pending_tasks": pending, "api_version": "1.4", "task_types": list(TASK_TYPES)}


TASK_TYPES = ("keyword", "title", "author", "qianfan")
DEFAULT_TYPES = ("keyword", "title")  # 没声明 types 的旧采集端只会领到这两种,不会领到自己不认识的任务


def parse_types(types: str) -> tuple[str, ...]:
    got = tuple(t for t in dict.fromkeys(x.strip() for x in (types or "").split(",")) if t in TASK_TYPES)
    return got or DEFAULT_TYPES


@router.get("/tasks")
def claim_tasks(request: Request, limit: int = 1, worker_name: str = Query("", alias="worker"), types: str = "",
                shop: str = "", shop_name: str = "", kinds: str = "", cv: int = 0):
    """采集端轮询:领取待抓任务。领走即标记为 running,超时未回传会自动退回。
    types:本采集端支持的任务类型,逗号分隔(keyword,title,author);不传只发 keyword 和 title。
    千帆任务:shop = 这个采集端登录的店铺 ID(shop_name 是店铺名),只领这个店铺的任务和不分店铺的任务;不传 shop 就领不到分店铺的任务。
    kinds = 这个采集端能做的千帆数据类别(account,notes,...),不传 = 都能做。"""
    want = parse_types(types)
    # cv = 采集端程序版本号:设置里的 collector_min_cv 调高后,更旧的千帆采集端收到 401 并退出。
    # 用来清掉升级时残留的旧版采集脚本(它们会抢任务,又不认识新的任务类别,抢到就报「不支持」)
    need = int(get_settings().get("collector_min_cv") or 0)
    if "qianfan" in want and cv < need:
        raise HTTPException(401, f"采集端版本过旧(需要 cv >= {need}),请重启采集端")
    shop = shop.strip()[:64]
    qf_kinds = {k.strip() for k in kinds.split(",") if k.strip()} or None
    if shop:
        qianfan.register_shop(shop, shop_name.strip())
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        _expire_stale(c)
        on = (c.execute("SELECT value FROM settings WHERE key='library_collect_enabled'").fetchone() or ["1"])[0] != "0"
        # 用户的任务永远排在前面;笔记库沉淀任务只在没有用户任务时领取(也可以在管理端暂停)
        rows = c.execute(f"SELECT * FROM crawl_tasks WHERE status='pending' AND type IN ({','.join('?' * len(want))}) "
                         + ("" if on else "AND COALESCE(purpose,'project')!='library' ")
                         + "ORDER BY CASE COALESCE(purpose,'project') WHEN 'library' THEN 2 WHEN 'qianfan' THEN 1 ELSE 0 END, id LIMIT 200", want).fetchall()
        rows = [r for r in rows if _qf_ok(r, shop, qf_kinds)][:max(1, min(limit, 20))]
        for r in rows:
            c.execute("UPDATE crawl_tasks SET status='running',worker=?,claimed_at=datetime('now'),claim_start=datetime('now') WHERE id=?",
                      (worker_name, r["id"]))
        _heartbeat(c, request, worker_name, len(rows))
    return {"tasks": [_task_out(r) for r in rows]}


def _qf_ok(r, shop: str, kinds: set | None) -> bool:
    """千帆任务只给对应店铺的采集端;采集端声明了能做的类别就只给这些类别。"""
    if r["type"] != "qianfan":
        return True
    if (qianfan._task_params(r).get("shop") or "") not in ("", shop):
        return False
    return kinds is None or r["query"] in kinds


def _task_out(r) -> dict:
    if r["type"] == "qianfan":  # 千帆数据同步:query 是数据类别(account/notes/hot_notes/hot_topics/hot_keywords),params 是周期和品类
        try:
            params = json.loads(r["params"] or "{}")
        except Exception:
            params = {}
        return {"id": r["id"], "type": "qianfan", "query": r["query"], "limit": 0, "params": params}
    return {"id": r["id"], "type": r["type"], "query": r["query"], "limit": r["lim"],
            "max_age_days": r["max_age_days"] or 0, "note_type": r["note_type"] or "image", "sort": r["sort"] or "popularity", "skip_known": True}


@router.post("/qianfan/ingest")
async def qianfan_ingest(request: Request):
    """千帆后台数据回传:{"task_id":12,"kind":"hot_notes","period":"7d","category":"美食","items":[...]};
    kind=account 时传 {"account":{...}};抓取失败传 {"task_id":12,"kind":"...","error":"原因"}。字段名中英文表头都认。"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "需要 JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "需要 JSON 对象")
    try:
        return qianfan.ingest(body)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/notes")
async def receive_notes(request: Request):
    """标准格式:{"task_id":1,"notes":[...],"done":true,"error":""}
    兼容格式:[{"task_id":1, ...笔记字段}, ...],按 task_id 分组并视为完成。"""
    body = await request.json()
    with conn() as c:  # 回传笔记也算心跳(采集端一条条读笔记时可能很久不 ping),同时给这个任务续租
        first = body[0] if isinstance(body, list) and body else body
        tid0 = int((first or {}).get("task_id") or 0) if isinstance(first, dict) else 0
        _heartbeat(c, request, _resolve_worker(c, request.query_params.get("worker", "") or (body.get("worker", "") if isinstance(body, dict) else ""), tid0), 0)
        if tid0:
            c.execute("UPDATE crawl_tasks SET claimed_at=datetime('now') WHERE id=? AND status='running'", (tid0,))
    if isinstance(body, list):
        groups: dict[int, list] = {}
        for n in body:
            groups.setdefault(int(n.get("task_id") or 0), []).append(n)
        batches = [{"task_id": k, "notes": v, "done": True} for k, v in groups.items()]
    else:
        batches = [body]
    result = []
    for b in batches:
        tid = int(b.get("task_id") or 0)
        task = q_one("SELECT t.*, p.track AS ptrack FROM crawl_tasks t LEFT JOIN projects p ON p.id=t.project_id WHERE t.id=?", tid)
        if not task:
            raise HTTPException(404, f"task_id {tid} 不存在")
        notes, skipped_old = split_by_age(b.get("notes") or [], int(task.get("max_age_days") or 0))  # 兜底:太旧的丢掉
        keyword = task["query"]
        if task["type"] == "author":  # 博主任务:关键词记成「博主:昵称」,方便在笔记库里按博主筛选
            name = next((str(n.get("author")) for n in notes if isinstance(n, dict) and n.get("author")), "")
            keyword = "博主:" + (name or task["query"])[:40]
        r = save_notes(notes, project_id=task["project_id"], task_id=tid, track=task["ptrack"] or task["label"] or "",
                       keyword=keyword, source="collector", worker=task["worker"] or "")
        err, done = str(b.get("error") or ""), b.get("done", True)
        status = "failed" if err else "done" if done else "running"
        if (task.get("purpose") or "project") == "library":  # 沉淀任务不属于任何项目:统计这个任务看到几篇、其中几篇是笔记库里没有的
            q_exec("UPDATE crawl_tasks SET status=?, error=?, note_count=COALESCE(note_count,0)+?, new_count=COALESCE(new_count,0)+?,"
                   " finished_at=CASE WHEN ?='running' THEN finished_at ELSE datetime('now') END WHERE id=?",
                   status, err, r["new"] + r["updated"], r["new"], status, tid)
        else:
            q_exec("UPDATE crawl_tasks SET status=?, error=?, note_count=(SELECT COUNT(*) FROM project_notes WHERE task_id=?),"
                   " finished_at=CASE WHEN ?='running' THEN finished_at ELSE datetime('now') END WHERE id=?",
                   status, err, tid, status, tid)
        result.append({"task_id": tid, "saved": r["linked"], "duplicated": r["new"] + r["updated"] - r["linked"],
                       "new_in_library": r["new"], "skipped_old": skipped_old, "status": status})
    worker.wake_tagger()
    mirror.wake()  # 图片链接会过期,收到就立刻转存
    return {"ok": True, "results": result}


@router.post("/notes/known")
async def known_notes(request: Request):
    """采集端在搜索结果列表拿到一批笔记 ID 后先问一下:哪些已经在笔记库里了。
    已有的不用再点进详情页(省时间,也少触发风控);任务的 limit 按新笔记计算。
    请求:{"ids": ["笔记ID或链接", ...]}(一次最多 500 个);返回:{"known": [...], "new": [...]}"""
    body = await request.json()
    ids = [str(x).strip() for x in (body.get("ids") if isinstance(body, dict) else body) or [] if str(x).strip()][:500]
    with conn() as c:
        wname = _resolve_worker(c, request.query_params.get("worker", "") or (body.get("worker", "") if isinstance(body, dict) else ""))
        _heartbeat(c, request, wname, 0)
    have = set()
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        have |= {r["uid"] for r in q_all(f"SELECT uid FROM notes WHERE uid IN ({','.join('?' * len(chunk))})", *chunk)}
    q_exec("UPDATE collectors SET known_calls=COALESCE(known_calls,0)+1, known_at=?, known_total=COALESCE(known_total,0)+?, known_hit=COALESCE(known_hit,0)+? WHERE worker=?",
           time.time(), len(ids), len(have), wname)  # 记下采集端有没有用上这个接口,管理端的采集端列表里看得到
    return {"known": [i for i in ids if i in have], "new": [i for i in ids if i not in have]}
