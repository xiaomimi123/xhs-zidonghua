"""封面指纹的存储、后台计算和对号。原理和实测数据见 coverhash.py。

- 稿子的封面(drafts.images 里第一张,本地文件)和千帆笔记的封面(同步时采集端带回的 cover 地址,服务端下载)各算一个指纹,存在 cover_fp 表里。
- 计算都在后台线程里做(稿子封面几百张、笔记封面要下载),页面请求不会等它;还没算好的这一轮就只按标题对号。
- 对号(match):标题对不上的笔记,和还没对上的稿子比封面,要求「足够像」且「明显优于第二名」,并且账号不冲突、笔记发布时间不早于稿子生成时间。
- 手动绑定窗口(note_cover_dists):把封面像的笔记排在最前面,标「封面一致」,让人一点就绑。"""
import calendar
import datetime
import threading
import time

import httpx

from . import coverhash as ch
from . import logx, mirror
from .db import conn, q_all

RETRY_H = 6                 # 下载失败的封面,过多少小时再试
BATCH = 400                 # 一轮后台最多处理多少张笔记封面
_state = {"running": False, "last": 0.0}
_lock = threading.Lock()


# ---------- 存取 ----------
def get(kind: str, keys) -> dict:
    """{key: (dh, vec)},只返回算好的。"""
    keys = [k for k in dict.fromkeys(keys) if k]
    out = {}
    with conn() as c:
        for i in range(0, len(keys), 400):
            part = keys[i:i + 400]
            for r in c.execute(f"SELECT key, dh, vec FROM cover_fp WHERE kind=? AND key IN ({','.join('?' * len(part))}) AND vec IS NOT NULL AND LENGTH(vec)>0", [kind, *part]):
                dh = ch.from_hex(r["dh"])
                if dh is not None:
                    out[r["key"]] = (dh, bytes(r["vec"]))
    return out


def _known(kind: str) -> dict:
    """{key: (有指纹, 失败时间)},用来跳过已经算过的。"""
    with conn() as c:
        return {r["key"]: (bool(r["vec"]), r["at"], r["err"]) for r in c.execute("SELECT key, vec, at, err FROM cover_fp WHERE kind=?", (kind,))}


def put(kind: str, key: str, fp, src: str = "", err: str = ""):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO cover_fp(kind,key,dh,vec,src,err,at) VALUES(?,?,?,?,?,?,datetime('now'))",
                  (kind, key, ch.to_hex(fp[0]) if fp else "", fp[1] if fp else b"", src[:300], err[:100]))


def cover_file(images) -> str:
    """稿子的封面文件名(images 里第一张有内容的)。"""
    return next((i for i in (images or []) if i), "")


# ---------- 后台计算 ----------
def warm_drafts() -> int:
    from .imagegen import IMG_DIR
    known = _known("draft")
    n = 0
    for r in q_all("SELECT images FROM drafts WHERE images!='[]' ORDER BY id DESC"):
        f = cover_file(r["images"])
        if not f or f in known and known[f][0]:
            continue
        p = IMG_DIR / f
        if not p.is_file():
            continue
        fp = ch.fp_file(p)
        if fp:
            put("draft", f, fp)
            n += 1
    return n


def _retry_ok(at: str) -> bool:
    try:
        return time.time() - calendar.timegm(time.strptime(at, "%Y-%m-%d %H:%M:%S")) > RETRY_H * 3600   # at 是 UTC
    except (TypeError, ValueError):
        return True


def warm_notes(items: list[dict]) -> int:
    known = _known("note")
    todo = [it for it in items if it.get("note_id") and str(it.get("cover") or "").startswith("http")
            and not (str(it["note_id"]) in known and (known[str(it["note_id"])][0] or not _retry_ok(known[str(it["note_id"])][1])))][:BATCH]
    if not todo:
        return 0
    n = 0
    with httpx.Client(timeout=20) as cl:
        for it in todo:
            nid, url = str(it["note_id"]), str(it["cover"])
            try:
                fp = ch.fp_bytes(mirror.fetch(cl, url))
                put("note", nid, fp, url, "" if fp else "不是图片")
                n += 1 if fp else 0
            except Exception as e:   # 下载失败(链接过期 / 网络)记下来,过几个小时再试,不要每次都重下
                put("note", nid, None, url, str(e)[:100])
            time.sleep(0.25)
    return n


def _run():
    try:
        from . import qianfan
        d = warm_drafts()
        items, _ = qianfan.notes_all()
        n = warm_notes(items)
        if d or n:
            print(f"[封面指纹] 稿子 {d} 张、笔记 {n} 张已计算", flush=True)
    except Exception as e:
        logx.warn("封面指纹计算", e)
    finally:
        _state["running"] = False


def kick(force: bool = False):
    """后台算一轮(已经在跑、或刚算过不久就不重复)。同步完笔记、启动时、对号发现有没算的时候调用。"""
    with _lock:
        if _state["running"] or (not force and time.time() - _state["last"] < 120):
            return False
        _state.update(running=True, last=time.time())
    threading.Thread(target=_run, daemon=True, name="cover-fp").start()
    return True


# ---------- 对号 ----------
def _day(s: str) -> str:
    return str(s or "")[:10]


def _later_ok(note_day: str, draft_created: str) -> bool:
    """笔记的发布日期不能比稿子生成日期早一天以上(稿子是 UTC,笔记日期是北京时间,留一天余量)。"""
    nd, dd = _day(note_day), _day(draft_created)
    if not nd or not dd:
        return True
    try:
        return nd >= (datetime.date.fromisoformat(dd) - datetime.timedelta(days=1)).isoformat()
    except ValueError:
        return True


_cache = {"key": None, "out": {}}


def _fp_version():
    """指纹表有没有变:条数 + 最大行号(每次写入 INSERT OR REPLACE 都会生成新的行号,所以任何变化都能发现)。
    以前用 SUM(LENGTH(vec)) 要把几千个指纹大字段全读一遍,单这一句就 270 毫秒。"""
    with conn() as c:
        return tuple(c.execute("SELECT COUNT(*), MAX(rowid) FROM cover_fp").fetchone())


def match(notes: list[dict], drafts: list[dict]) -> dict[int, dict]:
    """notes:还没对上稿子的千帆笔记;drafts:还没对上笔记的稿子(带 id / images / account_id / created)。返回 {稿子 id: 笔记}。
    条件:封面足够像(coverhash.SAME)、明显优于第二名、账号不冲突、发布时间合理;一篇稿子只对一条笔记、一条笔记只对一篇稿子。
    结果按输入缓存(笔记、稿子封面、账号、日期和指纹表都没变就直接返回上次的):页面每次打开都会对号,不缓存要多花约 0.4 秒。"""
    key = (_fp_version(),
           tuple(sorted((str(n.get("note_id")), n.get("cover") or "", n.get("account_id") or "", n.get("published_at") or "") for n in notes if n.get("cover"))),
           tuple(sorted((d["id"], cover_file(d.get("images")), d.get("account_id") or "", _day(d.get("created"))) for d in drafts)))
    if _cache["key"] == key:
        return {k: dict(v) for k, v in _cache["out"].items()}
    out = _match(notes, drafts)
    _cache.update(key=key, out=out)
    return {k: dict(v) for k, v in out.items()}


def _match(notes: list[dict], drafts: list[dict]) -> dict[int, dict]:
    nfp = get("note", [str(n.get("note_id")) for n in notes if n.get("cover")])
    if not nfp:
        return {}
    files = {d["id"]: cover_file(d.get("images")) for d in drafts}
    dfp = get("draft", files.values())
    cands = [(d, dfp[files[d["id"]]]) for d in drafts if files.get(d["id"]) in dfp]
    if not cands:
        return {}
    picks = []
    for n in notes:
        fp = nfp.get(str(n.get("note_id")))
        if not fp:
            continue
        dists = []
        for d, (dh, vec) in cands:
            if d.get("account_id") and n.get("account_id") and d["account_id"] != n["account_id"]:
                continue
            if not _later_ok(n.get("published_at") or "", d.get("created") or ""):
                continue
            if ch.dh_dist(fp[0], dh) > ch.DH_MAX:
                continue
            dists.append((ch.vec_dist(fp[1], vec), d["id"]))
        dists.sort()
        if not dists or dists[0][0] > ch.SAME:
            continue
        best = dists[0][0]
        if len(dists) > 1 and dists[1][0] < max(2 * best, 2 * ch.SAME):   # 第二名也很像(同版式文字卡片 / 重复的封面):分不清,不自动对,留给人挑
            continue
        picks.append((best, n, dists[0][1]))
    picks.sort(key=lambda x: x[0])
    out, used = {}, set()
    for dist, n, did in picks:
        nid = str(n.get("note_id"))
        if did in out or nid in used:
            continue
        out[did] = {**n, "matched_by": "cover", "cover_d": round(dist, 3)}
        used.add(nid)
    return out


def note_fps(items: list[dict]) -> dict:
    """这批笔记的封面指纹(一次读出来,绑定窗口里很多篇稿子共用)。"""
    return get("note", [str(n.get("note_id")) for n in items if n.get("cover")])


def note_cover_dists(draft_images, items: list[dict], account_id: str = "", nfp: dict | None = None) -> dict[str, float]:
    """一篇稿子的封面和各条笔记封面的距离 {note_id: 距离},只要不超过 coverhash.LOOSE 的。手动绑定窗口用。"""
    f = cover_file(draft_images)
    dfp = get("draft", [f]) if f else {}
    if not dfp:
        if f:
            from .imagegen import IMG_DIR
            fp = ch.fp_file(IMG_DIR / f)   # 这一张还没算过:现算(一张图,很快)
            if fp:
                put("draft", f, fp)
                dfp = {f: fp}
        if not dfp:
            return {}
    dh, vec = dfp[f]
    nfp = nfp if nfp is not None else note_fps(items)
    out = {}
    for n in items:
        fp = nfp.get(str(n.get("note_id")))
        if not fp or ch.dh_dist(dh, fp[0]) > ch.DH_MAX * 2:
            continue
        if account_id and n.get("account_id") and n["account_id"] != account_id:
            continue
        d = ch.vec_dist(vec, fp[1])
        if d <= ch.LOOSE:
            out[str(n["note_id"])] = round(d, 3)
    return out
