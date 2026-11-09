"""千帆后台数据抓取:打开已登录的页面,监听页面自己发出的 JSON 接口,按字段别名抽取。
协议见 docs/采集端_千帆数据同步说明.md。

页面地址放在 collector/qianfan_pages.json(每个 query 一项),因为后台改版或你用的入口不同时只需改配置。
字段对不上时先跑:  python collector/collector.py --probe account  把接口原始 JSON 存到 collector/probe/,
再按里面真实的键名补 ALIASES。
"""
import json
import re
import time
from pathlib import Path

from xhs import CollectError, nap

HERE = Path(__file__).parent

# 归一化(小写、去下划线/空格)后匹配;前面的优先
ALIASES = {
    "name": ["nickname", "accountname", "name", "username", "昵称", "账号名"],
    "fans": ["fanscount", "fansnum", "totalfans", "fans", "粉丝数", "粉丝总数", "粉丝"],
    "new_fans": ["newfans", "fansincr", "increasefans", "fansadd", "新增粉丝", "涨粉数", "涨粉"],
    "notes": ["notecount", "notenum", "publishnotecount", "publishnum", "发布笔记数", "笔记数"],
    "views": ["viewcount", "impcount", "impnum", "impression", "readcount", "views", "曝光量", "曝光数", "观看量", "阅读量"],
    "likes": ["likecount", "likednum", "likes", "点赞数", "点赞量", "点赞"],
    "collects": ["collectcount", "collectednum", "collects", "收藏数", "收藏量", "收藏"],
    "comments": ["commentcount", "commentnum", "comments", "评论数", "评论量", "评论"],
    "shares": ["sharecount", "sharenum", "shares", "分享数", "分享量", "分享"],
    "visits": ["visitcount", "profilevisit", "homepagevisit", "visits", "主页访客", "访客数", "主页访问"],
    # 列表项
    "note_id": ["noteid", "id", "笔记id"],
    "title": ["title", "notetitle", "displaytitle", "笔记标题", "标题"],
    "published_at": ["publishtime", "posttime", "createtime", "time", "发布时间"],
    "author": ["authorname", "nickname", "username", "博主", "作者"],
    "heat": ["hotscore", "heatscore", "heat", "hot", "score", "热度", "热度值"],
    "rank": ["rank", "ranking", "排名"],
    "topic": ["topicname", "topic", "话题", "话题名称"],
    "keyword": ["keyword", "hotword", "word", "searchword", "关键词", "搜索词"],
    "topic_notes": ["notecount", "notenum", "笔记数"],
}


def _norm(k):
    return re.sub(r"[_\s\-]", "", str(k)).lower()


def _pick(d, field):
    """在一个 dict 里按别名找字段值;值为 dict(如 {value: 123})时取其 value。"""
    keys = {_norm(k): k for k in d}
    for a in ALIASES[field]:
        if a in keys:
            v = d[keys[a]]
            if isinstance(v, dict):
                v = v.get("value", v.get("count", None))
            if v not in (None, "") and not isinstance(v, (dict, list)):
                return v
    return None


def _walk_dicts(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk_dicts(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk_dicts(v)


def _all_lists(o):
    if isinstance(o, list):
        if o and all(isinstance(x, dict) for x in o):
            yield o
        for v in o:
            yield from _all_lists(v)
    elif isinstance(o, dict):
        for v in o.values():
            yield from _all_lists(v)


def extract_account(payloads):
    """遍历所有接口返回,每个字段取第一个命中的值。"""
    out = {}
    for p in payloads:
        for d in _walk_dicts(p):
            for f in ("name", "fans", "new_fans", "notes", "views", "likes", "collects", "comments", "shares", "visits"):
                if f not in out:
                    v = _pick(d, f)
                    if v is not None:
                        out[f] = v
    if not any(k != "name" for k in out):
        raise CollectError("账号数据没抓到任何指标:页面地址不对或接口字段变了,先运行 --probe account 看原始数据")
    return {"account": out}


_ITEM_FIELDS = {
    "notes": ["note_id", "title", "published_at", "views", "likes", "collects", "comments", "shares", "new_fans"],
    "hot_notes": ["title", "author", "likes", "collects", "comments", "heat", "rank"],
    "hot_topics": ["topic", "heat", "topic_notes", "rank"],
    "hot_keywords": ["keyword", "heat", "topic_notes", "rank"],
}
_MUST = {"notes": "title", "hot_notes": "title", "hot_topics": "topic", "hot_keywords": "keyword"}


def extract_items(kind, payloads):
    """在所有返回里找「行数最多、且标题/名称字段能命中」的那个数组,逐行按别名映射。"""
    must, best = _MUST[kind], []
    for p in payloads:
        for lst in _all_lists(p):
            rows = [r for r in lst if _pick(r, must) is not None]
            if len(rows) > len(best):
                best = rows
    if not best:
        raise CollectError(f"{kind} 没找到数据列表:页面地址不对或接口字段变了,先运行 --probe {kind}")
    items = []
    for i, r in enumerate(best[:300], 1):
        it = {}
        for f in _ITEM_FIELDS[kind]:
            v = _pick(r, f)
            if v is not None:
                key = {"topic_notes": "notes"}.get(f, f)
                if kind == "hot_topics" and f == "topic":
                    key = "name"
                it[key] = v
        it.setdefault("rank", i)
        items.append(it)
    return {"items": items}


# ---------- 浏览器侧 ----------

def load_pages():
    f = HERE / "qianfan_pages.json"
    if not f.exists():
        raise CollectError("缺少 collector/qianfan_pages.json")
    return json.loads(f.read_text(encoding="utf-8"))


def capture(page, url, wait=8):
    """打开 url,收集页面期间所有 JSON 返回,返回 [(接口地址, 数据)]。"""
    got = []

    def on_resp(r):
        try:
            if "json" in (r.headers.get("content-type") or "") and r.status == 200:
                got.append((r.url, r.json()))
        except Exception:
            pass

    page.on("response", on_resp)
    try:
        page.goto(url, wait_until="domcontentloaded")
        nap(2, 3)
        if any(w in page.url for w in ("login", "passport", "signin")):
            raise CollectError("千帆登录已过期,需要重新登录(运行 login.bat)")
        try:
            page.wait_for_load_state("networkidle", timeout=wait * 1000)
        except Exception:
            pass
        time.sleep(2)
    finally:
        page.remove_listener("response", on_resp)
    return got


def run(page, task, probe=False):
    """执行一个千帆任务,返回要 POST 到 /api/qianfan/ingest 的 body(不含 task_id)。"""
    query, params = task["query"], task.get("params") or {}
    period, category = params.get("period", "7d"), params.get("category", "")
    cfg = load_pages().get(query)
    if not cfg or not cfg.get("url"):
        raise CollectError(f"qianfan_pages.json 里没配置 {query} 的页面地址")
    url = cfg["url"].format(period=period, category=category)
    got = capture(page, url)
    if probe:
        d = HERE / "probe" / query
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("*.json"):
            f.unlink()
        for i, (u, data) in enumerate(got):
            (d / f"{i:02d}.json").write_text(json.dumps({"url": u, "data": data}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已保存 {len(got)} 个接口返回到 {d}")
    match = cfg.get("match") or []
    payloads = [d for u, d in got if not match or any(m in u for m in match)]
    if not payloads:
        raise CollectError(f"{query}:页面没有产生可用的数据接口(检查页面地址/登录状态,或 match 配置)")
    if query == "account":
        body = extract_account(payloads)
    else:
        body = extract_items(query, payloads)
    body["kind"] = query
    if query.startswith("hot_"):
        body["period"], body["category"] = period, category   # 原样回传下发的值
    return body
