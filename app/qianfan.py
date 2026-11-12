"""千帆后台数据:账号数据、我们发布笔记的表现、热门笔记榜、热门话题榜、热门关键词榜。

数据由采集端登录千帆后台读取后回传(POST /api/qianfan/ingest),也可以在管理端粘贴 JSON 导入。
工作台按设置的间隔自动给采集端下发同步任务(type=qianfan),采集端没空时排在用户任务和笔记库沉淀任务之间。
存储是「快照」:每次同步存一份完整数据,账号数据保留历史用来画趋势,榜单只看最新一份。"""
import json
import threading
import time

from .db import conn, get_settings, q_all, q_exec, q_one
from .notes import _grams, num

KINDS = {"account": "账号数据", "notes": "笔记表现", "hot_notes": "热门笔记榜", "hot_topics": "热门话题榜", "hot_keywords": "热门关键词榜"}
HOT = ("hot_notes", "hot_topics", "hot_keywords")

# 字段别名:采集端传英文或页面上的中文表头都认
ALIASES = {
    "name": ["name", "nickname", "账号", "账号名称", "昵称"],
    "fans": ["fans", "粉丝", "粉丝数", "粉丝总数", "总粉丝"],
    "new_fans": ["new_fans", "新增粉丝", "净增粉丝", "涨粉", "涨粉数"],
    "notes": ["notes", "note_count", "笔记数", "发布笔记数", "笔记总数"],
    "views": ["views", "impressions", "曝光", "曝光量", "展现量", "展现"],
    "reads": ["reads", "阅读", "阅读量", "观看", "观看量", "播放量"],
    "likes": ["likes", "点赞", "点赞数", "赞"],
    "collects": ["collects", "收藏", "收藏数", "藏"],
    "comments": ["comments", "评论", "评论数", "评"],
    "shares": ["shares", "分享", "转发", "分享数", "转发数"],
    "visits": ["visits", "profile_visits", "主页访客", "主页访问", "主页访问量"],
    "likes_total": ["likes_total", "获赞", "总获赞", "获赞总数"],
    "collects_total": ["collects_total", "总收藏", "收藏总数"],
    "fans_delta": ["fans_delta", "粉丝变化", "粉丝增长", "粉丝净增"],
    "title": ["title", "标题", "笔记标题", "笔记", "name", "话题", "话题名称"],
    "note_id": ["note_id", "笔记id", "笔记ID", "id"],
    "published_at": ["published_at", "发布时间", "发布日期"],
    "author": ["author", "作者", "博主", "发布者"],
    "heat": ["heat", "热度", "热度值", "热力", "指数", "搜索指数", "index", "score"],
    "keyword": ["keyword", "关键词", "热词", "搜索词", "词"],
    "topic": ["topic", "话题", "话题名称", "话题名"],
    "rank": ["rank", "排名", "名次", "序号"],
    "url": ["url", "链接", "笔记链接", "link"],
    "cover": ["cover", "封面", "封面图"],
    "trend": ["trend", "趋势", "涨跌"],
    "account_id": ["account_id", "user_id", "账号id", "账号ID"],
    "account_name": ["account_name", "账号名称", "所属账号"],
    "red_id": ["red_id", "小红书号"],
}
NUMERIC = {"fans", "new_fans", "notes", "views", "reads", "likes", "collects", "comments", "shares", "visits", "likes_total",
           "collects_total", "fans_delta", "heat", "rank"}


def _pick(d: dict, key: str):
    for a in ALIASES.get(key, [key]):
        if a in d and d[a] not in (None, ""):
            return d[a]
    return None


def _norm_row(d: dict, keys: list[str]) -> dict:
    out = {}
    for k in keys:
        v = _pick(d, k)
        if v is not None:
            out[k] = num(v) if k in NUMERIC else (str(v).strip() if not isinstance(v, (dict, list)) else v)
    return out


def _norm_account(raw: dict) -> dict:
    acc = _norm_row(raw, ["name", "fans", "new_fans", "fans_delta", "notes", "views", "reads", "likes", "collects", "comments",
                          "shares", "visits", "likes_total", "collects_total", "account_id", "red_id"])
    if acc.get("account_id"):
        acc["account_id"] = acc["account_id"][:64]
    return acc


def normalize(kind: str, body: dict) -> dict:
    """把采集端传来的数据规范成统一格式:{"account": {...}} 或 {"items": [...]}。字段缺了就缺着,不编造。"""
    if kind == "account":
        raw = body.get("account") if isinstance(body.get("account"), dict) else {k: v for k, v in body.items() if k not in ("kind", "task_id")}
        acc = _norm_account(raw)
        if not acc:
            raise ValueError("账号数据是空的")
        return {"account": acc}
    items = body.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("items 不能为空")
    rows = []
    for pos, it in enumerate(items[:1000], 1):
        if not isinstance(it, dict):
            continue
        if kind == "notes":
            r = _norm_row(it, ["note_id", "title", "published_at", "views", "reads", "likes", "collects", "comments", "shares", "new_fans", "url", "cover",
                                  "account_id", "account_name"])
            if not r.get("title"):
                continue
            r["engagement"] = r.get("likes", 0) + r.get("collects", 0) + r.get("comments", 0)
        elif kind == "hot_notes":
            r = _norm_row(it, ["rank", "title", "author", "note_id", "likes", "collects", "comments", "heat", "url", "cover"])
            if not r.get("title"):
                continue
        else:  # hot_topics / hot_keywords
            r = _norm_row(it, ["rank", "heat", "views", "notes", "trend"])
            name = _pick(it, "keyword" if kind == "hot_keywords" else "topic") or _pick(it, "title")
            if not name:
                continue
            r["name"] = str(name).strip().lstrip("#")
        r.setdefault("rank", pos)
        rows.append(r)
    if not rows:
        raise ValueError("没有可用的数据行(缺少标题 / 名称)")
    rows.sort(key=lambda r: r["rank"])
    return {"items": rows}


def register_shop(shop_id: str, name: str = "") -> None:
    """采集端领任务时报上自己服务的店铺;第一次出现的店铺登记下来。
    第一个店铺登记时,之前没分店铺存下的数据(shop_id 为空)都认领给它。"""
    shop_id = (shop_id or "").strip()[:64]
    if not shop_id:
        return
    row = q_one("SELECT shop_id FROM qf_shops WHERE shop_id=?", shop_id)
    if row:
        q_exec("UPDATE qf_shops SET last_seen=datetime('now'), name=CASE WHEN ?!='' THEN ? ELSE name END WHERE shop_id=?", name, name, shop_id)
        return
    first = not q_one("SELECT 1 FROM qf_shops LIMIT 1")
    q_exec("INSERT INTO qf_shops(shop_id, name) VALUES(?,?)", shop_id, name[:60])
    if first:
        q_exec("UPDATE qf_snapshots SET shop_id=? WHERE shop_id=''", shop_id)


def shops() -> list[dict]:
    """登记过的店铺。online = 采集端 150 秒内来领过这个店铺的任务(同一个千帆账号同一时间只能登录一个店铺,所以同一时刻通常只有一个在线);
    last_sync = 这个店铺最近一次同步到数据的时间;pending = 等着这个店铺采集端领取的同步任务数。"""
    rows = q_all("SELECT shop_id, name, first_seen, last_seen, (last_seen > datetime('now','-150 seconds')) AS online FROM qf_shops ORDER BY first_seen, shop_id")
    synced = {r["shop_id"]: r["at"] for r in q_all("SELECT shop_id, MAX(snap_at) at FROM qf_snapshots WHERE shop_id!='' GROUP BY shop_id")}
    pending: dict[str, int] = {}
    for r in q_all("SELECT params FROM crawl_tasks WHERE type='qianfan' AND status IN ('pending','running')"):
        sid = _task_params(r).get("shop") or ""
        if sid:
            pending[sid] = pending.get(sid, 0) + 1
    return [{**r, "online": bool(r["online"]), "last_sync": synced.get(r["shop_id"], ""), "pending": pending.get(r["shop_id"], 0)} for r in rows]


def _shop_names() -> dict[str, str]:
    return {r["shop_id"]: r["name"] for r in shops()}


def _adopt_legacy(acc: dict) -> None:
    """以前没带账号 ID 存下的账号快照,按昵称认领给第一次带 ID 回传的同名账号,趋势不断档。"""
    if not acc.get("account_id") or not acc.get("name"):
        return
    for r in q_all("SELECT id, data FROM qf_snapshots WHERE kind='account' AND account_id=''"):
        d = r["data"] or {}
        if not d.get("sample") and (d.get("account") or {}).get("name") == acc["name"]:
            q_exec("UPDATE qf_snapshots SET account_id=? WHERE id=?", acc["account_id"], r["id"])


def _store_account(acc: dict, sample: bool = False, shop_id: str = '') -> int:
    data = {"account": acc}
    if sample:
        data["sample"] = True
    aid = acc.get("account_id", "")
    if not sample:
        _adopt_legacy(acc)
    snap = q_exec("INSERT INTO qf_snapshots(kind,scope,period,data,account_id,shop_id) VALUES('account','','',?,?,?)",
                  json.dumps(data, ensure_ascii=False), aid, shop_id).lastrowid
    if not sample:
        q_exec("DELETE FROM qf_snapshots WHERE kind='account' AND data LIKE '%\"sample\": true%'")  # 真实数据来了,示例数据就退场
        q_exec("DELETE FROM qf_snapshots WHERE kind='account' AND account_id=? AND snap_at < datetime('now','-180 days')", aid)
    return snap


def ingest(body: dict) -> dict:
    """保存一份快照,并把对应的同步任务标成完成(带了 error 就标成失败)。
    account 类可以一次回传多个账号:{"kind":"account","accounts":[{account_id,name,fans,...}, ...]},每个账号各存一份快照。"""
    kind = str(body.get("kind") or "")
    if kind == "buyers":   # 买手广场达人:进达人库(按达人去重累积),不走快照
        return _ingest_buyers(body)
    if kind == "buyer_contact":   # 某位达人的联系方式(员工在达人库里点了「获取联系方式」)
        return _ingest_contact(body)
    if kind not in KINDS:
        raise ValueError(f"kind 必须是 {' / '.join(KINDS)} 之一")
    tid = int(body.get("task_id") or 0)
    err = str(body.get("error") or "").strip()
    if err:
        if tid:
            q_exec("UPDATE crawl_tasks SET status='failed', error=?, finished_at=datetime('now') WHERE id=? AND type='qianfan'", err[:300], tid)
        return {"ok": True, "stored": 0, "error": err}
    shop_id = str(body.get("shop_id") or "").strip()[:64]
    if shop_id:
        register_shop(shop_id, str(body.get("shop_name") or ""))
    if kind == "account" and isinstance(body.get("accounts"), list):
        accs = [a for a in (_norm_account(x) for x in body["accounts"] if isinstance(x, dict)) if a]
        if not accs:
            raise ValueError("accounts 不能为空")
        snap = 0
        for a in accs:
            snap = _store_account(a, bool(body.get("sample")), shop_id)
        n = len(accs)
    else:
        data = normalize(kind, body)
        if data.get("sample") or body.get("sample"):
            data["sample"] = True
        scope, period = str(body.get("scope") or body.get("category") or "").strip()[:20], str(body.get("period") or "").strip()[:10]
        if kind == "account":
            snap = _store_account(data["account"], bool(data.get("sample")), shop_id)
        else:
            snap = q_exec("INSERT INTO qf_snapshots(kind,scope,period,data,shop_id) VALUES(?,?,?,?,?)", kind, scope, period, json.dumps(data, ensure_ascii=False), shop_id).lastrowid
            if not data.get("sample"):
                q_exec("DELETE FROM qf_snapshots WHERE kind=? AND scope=? AND period=? AND shop_id=? AND data LIKE '%\"sample\": true%'", kind, scope, period, shop_id)  # 榜单和笔记表现只看最新一份,历史留 10 份够对比
                q_exec("DELETE FROM qf_snapshots WHERE kind=? AND scope=? AND period=? AND shop_id=? AND id NOT IN (SELECT id FROM qf_snapshots WHERE kind=? AND scope=? AND period=? AND shop_id=? ORDER BY id DESC LIMIT 10)",
                       kind, scope, period, shop_id, kind, scope, period, shop_id)
        n = 1 if kind == "account" else len(data["items"])
    if tid:
        q_exec("UPDATE crawl_tasks SET status='done', error='', note_count=?, finished_at=datetime('now') WHERE id=? AND type='qianfan'", n, tid)
    if kind == "notes" and not body.get("sample"):
        start_tagging()   # 新笔记自动在后台归类,选题分析里直接能看
    return {"ok": True, "stored": n, "snapshot": snap}


def _ingest_contact(body: dict) -> dict:
    from . import kol
    tid, err = int(body.get("task_id") or 0), str(body.get("error") or "").strip()
    cid = str(body.get("creator_id") or "")
    if not cid:
        raise ValueError("缺少 creator_id")
    kol.save_contact(cid, str(body.get("phone") or ""), str(body.get("wechat") or ""), err)
    if err.startswith("风控"):   # 千帆出现风控 / 频率限制提示:立刻停止获取联系方式,排队的任务作废,等管理员确认
        kol.pause_contact(err)
    if tid:
        if err:
            q_exec("UPDATE crawl_tasks SET status='failed', error=?, finished_at=datetime('now') WHERE id=? AND type='qianfan'", err[:300], tid)
        else:
            q_exec("UPDATE crawl_tasks SET status='done', error='', note_count=1, finished_at=datetime('now') WHERE id=? AND type='qianfan'", tid)
    return {"ok": True, "stored": 0 if err else 1}


def _ingest_buyers(body: dict) -> dict:
    from . import kol
    tid, err = int(body.get("task_id") or 0), str(body.get("error") or "").strip()
    if err:
        if tid:
            q_exec("UPDATE crawl_tasks SET status='failed', error=?, finished_at=datetime('now') WHERE id=? AND type='qianfan'", err[:300], tid)
        return {"ok": True, "stored": 0, "error": err}
    items = body.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("items 不能为空")
    r = kol.upsert(items, str(body.get("category") or body.get("scope") or ""))
    if tid:
        q_exec("UPDATE crawl_tasks SET status='done', error='', note_count=?, new_count=?, finished_at=datetime('now') WHERE id=? AND type='qianfan'", len(items), r["new"], tid)
    return {"ok": True, "stored": len(items), **r}


# ---------- 查询 ----------
def latest(kind: str, scope: str = "", period: str = "", shop: str = "") -> dict | None:
    """某个店铺(默认 = 没分店铺的老数据)某个范围最新的一份快照。"""
    return q_one("SELECT id, snap_at, scope, period, data FROM qf_snapshots WHERE kind=? AND scope=? AND period=? AND shop_id=? ORDER BY id DESC LIMIT 1", kind, scope, period, shop)


def scopes(kind: str, shop: str = "") -> list[dict]:
    return q_all("SELECT scope, period, MAX(snap_at) at FROM qf_snapshots WHERE kind=? AND shop_id=? GROUP BY scope, period ORDER BY scope, period", kind, shop)


def latest_any(kind: str, scope: str = "", period: str = "") -> dict | None:
    """不分店铺的数据(平台话题榜):取任何店铺最新的一份。"""
    return q_one("SELECT id, snap_at, scope, period, data FROM qf_snapshots WHERE kind=? AND scope=? AND period=? ORDER BY id DESC LIMIT 1", kind, scope, period)


def scopes_any(kind: str) -> list[dict]:
    return q_all("SELECT scope, period, MAX(snap_at) at FROM qf_snapshots WHERE kind=? GROUP BY scope, period ORDER BY scope, period", kind)


def notes_all(shop: str = "") -> tuple[list[dict], str]:
    """各店铺最新一份「笔记表现」合在一起(每篇带 shop_id)。shop 不为空只取这个店铺。返回 (笔记, 数据时间)。"""
    items, at = [], ""
    for r in q_all("SELECT DISTINCT shop_id FROM qf_snapshots WHERE kind='notes'"):
        if shop and r["shop_id"] != shop:
            continue
        snap = latest("notes", "", "", r["shop_id"])
        if snap:
            items += [{**it, "shop_id": r["shop_id"]} for it in (snap["data"] or {}).get("items", [])]
            at = max(at, snap["snap_at"])
    return items, at


def series(limit: int = 90, account_id: str | None = None) -> list[dict]:
    """账号数据历史(趋势)。account_id 为 None 时不区分账号(兼容旧调用),传了就只取这个账号。"""
    if account_id is None:
        rows = q_all("SELECT snap_at, data, shop_id FROM qf_snapshots WHERE kind='account' ORDER BY id DESC LIMIT ?", limit)
    else:
        rows = q_all("SELECT snap_at, data, shop_id FROM qf_snapshots WHERE kind='account' AND account_id=? ORDER BY id DESC LIMIT ?", account_id, limit)
    return [{"at": r["snap_at"], **((r["data"] or {}).get("account") or {}), "shop_id": r["shop_id"]} for r in reversed(rows)]


def accounts(shop: str | None = None) -> list[dict]:
    """每个账号最新一份数据(+ 上一份用来算涨跌),粉丝多的排前面。没带账号 ID 的旧数据归在 account_id='' 下。"""
    out = []
    for r in q_all("SELECT DISTINCT account_id FROM qf_snapshots WHERE kind='account'"):
        s = series(2, r["account_id"])
        if s:
            out.append({**s[-1], "account_id": r["account_id"], "prev": s[-2] if len(s) > 1 else None})
    # 带 ID 的账号已经覆盖的昵称,就不再列没 ID 的旧数据
    named = {a.get("name") for a in out if a["account_id"]}
    out = [a for a in out if a["account_id"] or a.get("name") not in named]
    names = _shop_names()
    for a in out:
        a["shop_name"] = names.get(a.get("shop_id", ""), "")
    if shop:
        out = [a for a in out if a.get("shop_id", "") == shop]
    out.sort(key=lambda a: a.get("fans") or 0, reverse=True)
    return out


def _norm_title(t: str) -> str:
    import re
    return re.sub(r"[\W_]+", "", (t or "").lower())


def account_name(account_id: str) -> str | None:
    """账号 ID → 昵称;千帆账号列表里没有这个 ID 返回 None。"""
    return next((a.get("name") or "" for a in accounts() if a["account_id"] == account_id), None)


def match_drafts(items: list[dict]) -> dict[int, dict]:
    """笔记表现和我们生成的稿子对上号:标题去掉表情标点后一样,或 3 字片段重合 ≥ 80%。返回 {草稿 id: 表现数据}。
    稿子指定了发布账号、笔记也带账号时,只在同一个账号里对,避免别的账号的同标题笔记串号。"""
    drafts = q_all("SELECT id, title, account_id, qf_note_id FROM drafts WHERE title!=''")
    by_note = {str(it.get("note_id")): it for it in items if it.get("note_id")}
    out, taken = {}, set()
    for d in drafts:   # 手动对上的优先,不再按标题猜
        if d["qf_note_id"] and d["qf_note_id"] in by_note:
            out[d["id"]] = by_note[d["qf_note_id"]]
            taken.add(d["qf_note_id"])
    drafts = [d for d in drafts if d["id"] not in out]
    norm = {d["id"]: _norm_title(d["title"]) for d in drafts}
    grams = {i: _grams(t) for i, t in norm.items() if t}
    acct = {d["id"]: d["account_id"] or "" for d in drafts}
    # 索引:标题全等 → 稿子(保持原来的先后顺序);3 字片段 → 含它的稿子。
    # 原来每条笔记都扫一遍所有稿子(稿子 × 笔记,几百篇稿子 × 几千条笔记要循环上百万次),现在只看和它有重合片段的稿子。
    by_norm, inv = {}, {}
    for i, g in grams.items():
        by_norm.setdefault(norm[i], []).append(i)
        for x in g:
            inv.setdefault(x, []).append(i)
    for it in items:
        t = _norm_title(it.get("title", ""))
        if not t or str(it.get("note_id") or "") in taken:
            continue
        aid = it.get("account_id", "")
        ok = lambda i: i not in out and (not acct[i] or not aid or acct[i] == aid)
        did = next((i for i in by_norm.get(t, ()) if ok(i)), 0)
        if not did:
            g = _grams(t)
            if g:
                hits = {}
                for x in g:
                    for i in inv.get(x, ()):
                        hits[i] = hits.get(i, 0) + 1
                best = (0, 0)
                for i, c in hits.items():
                    if 5 * c < 4 * len(g) or not ok(i):   # Jaccard ≥ 0.8 的必要条件:共有片段 ≥ 0.8 × 笔记的片段数
                        continue
                    score = c / len(g | grams[i])
                    if (score, i) > best:   # 分数相同取编号大的,和原来 max() 的结果一致
                        best = (score, i)
                did = best[1] if best[0] >= 0.8 else 0
        if did:
            out[did] = it
    return out


# ---------- 选题分析:按账号看哪类选题表现好 ----------
_tagging = {"running": False, "done": 0, "total": 0, "error": ""}
_tag_lock = threading.Lock()


def _note_key(it: dict) -> str:
    return str(it.get("note_id") or "") or "t:" + _norm_title(it.get("title", ""))


def _latest_notes() -> list[dict]:
    return notes_all()[0]


def untagged() -> list[dict]:
    """还没归类完整(缺内容类型/主题,或缺选题角度)的笔记。"""
    done = {r["note_id"] for r in q_all("SELECT note_id FROM qf_note_tags WHERE angle!=''")}
    seen, out = set(), []
    for it in _latest_notes():
        k = _note_key(it)
        if k not in done and k not in seen and it.get("title"):
            seen.add(k)
            out.append(it)
    return out


def tag_notes_now(batch: int = 15) -> int:
    """给还没归类的笔记归类:内容类型 + 主题(沿用已有写法)+ 选题角度(固定一套)。每批 batch 篇调 AI。返回本次处理的篇数。"""
    from . import llm
    todo = untagged()
    _tagging.update(done=0, total=len(todo))
    have = {r["note_id"]: r for r in q_all("SELECT note_id, ctype, topic, angle FROM qf_note_tags")}
    n = 0
    for i in range(0, len(todo), batch):
        part = todo[i:i + batch]
        keys = [_note_key(it) for it in part]
        fresh = [j for j, k in enumerate(keys) if k not in have]
        if fresh:
            known = [r["topic"] for r in q_all("SELECT topic, COUNT(*) c FROM qf_note_tags GROUP BY topic ORDER BY c DESC LIMIT 40")]
            got = llm.tag_notes([{"id": j, "title": part[j]["title"]} for j in fresh], known_topics=known)
            for j in fresh:
                if got.get(j):
                    have[keys[j]] = {"ctype": got[j]["type"], "topic": got[j]["topic"], "angle": ""}
        angles = llm.angle_notes([{"id": j, "title": it["title"]} for j, it in enumerate(part)])
        for j, it in enumerate(part):
            t = have.get(keys[j])
            if t and angles.get(j):
                q_exec("INSERT OR REPLACE INTO qf_note_tags(note_id, title, ctype, topic, angle) VALUES(?,?,?,?,?)",
                       keys[j], it["title"][:200], t["ctype"], t["topic"], angles[j])
                have[keys[j]] = {**t, "angle": angles[j]}
                n += 1
        _tagging["done"] = min(i + batch, len(todo))
    return n


def start_tagging() -> dict:
    """后台给新笔记分类(没配 DeepSeek Key 或已在跑就什么都不做)。返回当前状态。"""
    if not (get_settings().get("deepseek_api_key") or "").strip():
        return {**_tagging, "error": "还没有配置 DeepSeek Key"}
    with _tag_lock:
        if _tagging["running"] or not untagged():
            return dict(_tagging)
        _tagging.update(running=True, error="")

    def run():
        try:
            tag_notes_now()
        except Exception as e:
            _tagging["error"] = str(e)[:200]
        finally:
            _tagging["running"] = False
    threading.Thread(target=run, daemon=True, name="qf-tagger").start()
    return dict(_tagging)


def _trimmed_mean(vals: list[float], frac: float = 0.05) -> float:
    """去掉最高的 frac 再求平均(每 20 篇才去 1 篇,样本少时就是普通平均)。几篇爆款不会把整个账号的平均线拉高。"""
    v = sorted(vals)
    v = v[:len(v) - int(len(v) * frac)]
    return sum(v) / len(v) if v else 0.0


def _agg(rows: list[dict], base: float, metric: str) -> dict:
    n = len(rows)
    sv = lambda k: sum(r.get(k) or 0 for r in rows)
    eng = [(r.get("likes") or 0) + (r.get("collects") or 0) + (r.get("comments") or 0) for r in rows]
    best = max(zip(eng, rows), key=lambda x: (x[0], x[1].get("views") or 0))[1]
    eng_avg, views_avg = sum(eng) / n, sv("views") / n
    score = eng_avg if metric == "eng" else views_avg          # 账号整体互动太少时改用阅读量比较
    lift = score / base if base else None
    enough = (sum(eng) if metric == "eng" else sv("views")) >= 10   # 总量太小的类别不下结论
    verdict = "样本少" if n < 3 else "数据太少" if not enough else "好" if lift and lift >= 1.3 else "弱" if lift is not None and lift <= 0.7 else ""
    return {"n": n, "views_avg": round(views_avg, 1), "likes_avg": round(sv("likes") / n, 1), "collects_avg": round(sv("collects") / n, 1),
            "comments_avg": round(sv("comments") / n, 1), "eng_avg": round(eng_avg, 1),
            "rate": round(sum(eng) / sv("views"), 4) if sv("views") else None, "lift": round(lift, 2) if lift is not None else None,
            "score": round(score, 1), "verdict": verdict, "best": {"title": best.get("title", ""), "likes": best.get("likes"), "views": best.get("views")}}


def topic_report(account_id: str = "", shop: str = "") -> dict:
    """某个账号(空 = 全部)的笔记,按选题角度 / 内容类型 / 主题汇总平均阅读和互动。
    lift = 该类的平均表现 ÷ 账号所有已归类笔记的平均;平均互动 ≥ 2 的账号用互动(赞+藏+评)比较,否则互动太少没有区分度,改用阅读量。
    互动数是笔记累计值,发得早的笔记会占便宜;样本少于 3 篇、总量太小的类别只作参考。"""
    items = [it for it in _latest_notes() if (not account_id or it.get("account_id", "") == account_id) and (not shop or it.get("shop_id", "") == shop)]
    tags = {r["note_id"]: r for r in q_all("SELECT note_id, ctype, topic, angle FROM qf_note_tags WHERE angle!=''")}
    tagged = [{**it, **{k: tags[_note_key(it)][k] for k in ("ctype", "topic", "angle")}} for it in items if _note_key(it) in tags]
    eng = lambda r: (r.get("likes") or 0) + (r.get("collects") or 0) + (r.get("comments") or 0)
    n = len(tagged)
    eng_avg = sum(eng(r) for r in tagged) / n if n else 0
    views_avg = sum(r.get("views") or 0 for r in tagged) / n if n else 0
    metric = "eng" if eng_avg >= 2 else "views"
    basev = _trimmed_mean([eng(r) for r in tagged] if metric == "eng" else [r.get("views") or 0 for r in tagged])   # 对比用的基线:去掉最高的 5%

    def group(key):
        g: dict[str, list] = {}
        for r in tagged:
            g.setdefault(r[key], []).append(r)
        out = [{"name": k, **_agg(v, basev, metric)} for k, v in g.items()]
        out.sort(key=lambda x: (x["n"] >= 3, x["score"], x["n"]), reverse=True)   # 样本够的排前面
        return out
    return {"account_id": account_id, "shop": shop, "total": len(items), "metric": metric,
            "base": {"n": n, "views_avg": round(views_avg, 1), "eng_avg": round(eng_avg, 1)},
            "by_angle": group("angle"), "by_type": group("ctype"), "by_topic": group("topic"),
            "untagged": len(items) - n, "tagging": dict(_tagging)}


# ---------- 按账号推荐:选题角度 / 搜索词 ----------
# 系统能如实写出来的角度。「促销活动」要真实的优惠和活动、「日常心情」「探店打卡」要真实的生活素材,系统不会编造,所以不推荐
WRITABLE = ("产品种草", "吃法搭配", "菜谱做法", "知识科普", "测评对比")
DEFAULT_ANGLES = ["产品种草", "吃法搭配"]


def style_titles(account_id: str, n: int = 5) -> list[str]:
    """这个账号里表现最好的(系统能写的角度的)笔记标题,给写作当选题和语气的参考。"""
    items = [it for it in _latest_notes() if not account_id or it.get("account_id", "") == account_id]
    tags = {r["note_id"]: r["angle"] for r in q_all("SELECT note_id, angle FROM qf_note_tags WHERE angle!=''")}
    rows = [it for it in items if tags.get(_note_key(it)) in WRITABLE and it.get("title")]
    eng = lambda r: (r.get("likes") or 0) + (r.get("collects") or 0) + (r.get("comments") or 0)
    use_eng = sum(eng(r) for r in rows) / len(rows) >= 2 if rows else False
    rows.sort(key=lambda r: (eng(r), r.get("views") or 0) if use_eng else (r.get("views") or 0, eng(r)), reverse=True)
    return [r["title"] for r in rows[:n]]


def recommend(account_id: str) -> dict:
    """新建任务时,按这个账号已有笔记的表现推荐选题角度(只推荐系统能如实写的角度),并说明依据。"""
    r = topic_report(account_id)
    name = next((a.get("name") or "" for a in accounts() if a["account_id"] == account_id), "")
    rows = [{"name": x["name"], "n": x["n"], "lift": x["lift"], "verdict": x["verdict"], "writable": x["name"] in WRITABLE,
             "views_avg": x["views_avg"], "eng_avg": x["eng_avg"]} for x in r["by_angle"]]
    # 只在系统能写的角度之间比较(几篇活动/日常类的爆款会把账号平均线拉高,让能写的角度都显得偏弱):基线 = 能写的角度的加权平均
    key = "eng_avg" if r["metric"] == "eng" else "views_avg"
    wr = [x for x in rows if x["writable"]]
    wbase = sum(x[key] * x["n"] for x in wr) / sum(x["n"] for x in wr) if wr else 0
    for x in rows:
        x["lift_w"] = round(x[key] / wbase, 2) if x["writable"] and wbase else None
    solid = [x for x in wr if x["n"] >= 3 and x["verdict"] != "数据太少" and (x["lift_w"] or 0) >= 0.9]
    pick = [x["name"] for x in sorted(solid, key=lambda x: x["lift_w"] or 0, reverse=True)][:2]
    best = next((x for x in sorted(rows, key=lambda x: x["lift"] or 0, reverse=True) if x["n"] >= 3 and x["verdict"] == "好"), None)
    notes = []
    if best and not best["writable"]:
        notes.append(f"这个账号数据里「{best['name']}」表现最好(×{best['lift']}),但这类内容需要真实的活动或生活素材,系统不会编造,所以没有推荐。")
    if not pick:
        pick = list(DEFAULT_ANGLES)
        notes.append("可写的角度里没有明显跑赢账号平均的,先用默认的产品种草 + 吃法搭配;多发几篇、数据多了会更准。")
    if r["base"]["n"] < 30:
        notes.append(f"这个账号只有 {r['base']['n']} 篇笔记,数据少,推荐只作参考。")
    return {"account_id": account_id, "name": name, "metric": r["metric"], "n": r["base"]["n"], "angles": rows,
            "recommended": pick, "note": " ".join(notes), "low_data": r["base"]["n"] < 30}


_kw_cache: dict[tuple, list[str]] = {}


def account_keywords(account_id: str) -> list[str]:
    """按账号的内容画像推荐采集用的搜索词(表现好的标题 + 主要主题 + 千帆热搜词,交给 AI 挑)。同一份数据只问一次 AI。"""
    from . import llm
    key = (account_id, notes_all()[1])
    if key in _kw_cache:
        return _kw_cache[key]
    titles = style_titles(account_id, 10)
    if not titles:
        return []
    tags = [r["topic"] for r in q_all("SELECT topic, COUNT(*) c FROM qf_note_tags GROUP BY topic ORDER BY c DESC LIMIT 6")]
    acc = next((a for a in accounts() if a["account_id"] == account_id), {})
    shop = acc.get("shop_id", "")   # 热搜词是店铺所在类目的,用这个账号所在店铺的
    hot = latest("hot_keywords", "", "7d", shop) or latest("hot_keywords", "", "", shop)
    hot_names = [i.get("name") for i in ((hot["data"] or {}).get("items", []) if hot else [])[:30] if i.get("name")]
    name = acc.get("name") or ""
    profile = (f"账号:{name}\n主要主题:{'、'.join(tags) or '无'}\n表现较好的笔记标题:\n" + "\n".join(f"- {t}" for t in titles)
               + f"\n近期热搜词:{'、'.join(hot_names) or '无'}")
    ks = llm.account_keywords(profile)
    _kw_cache[key] = ks
    return ks


def status() -> dict:
    s = get_settings()
    out = {"enabled": s.get("qf_enabled") == "1", "kinds": {}}
    for k, name in KINDS.items():
        snap = q_one("SELECT snap_at FROM qf_snapshots WHERE kind=? ORDER BY id DESC LIMIT 1", k)
        task = q_one("SELECT status, error, finished_at FROM crawl_tasks WHERE type='qianfan' AND query=? ORDER BY id DESC LIMIT 1", k)
        out["kinds"][k] = {"name": name, "last": snap["snap_at"] if snap else "", "task_status": task["status"] if task else "",
                           "error": task["error"] if task and task["status"] == "failed" else ""}
    return out


# ---------- 自动同步:按间隔给采集端下发任务 ----------
def _wanted(s: dict) -> list[tuple[str, str, str, float]]:
    kinds = [k for k in (s.get("qf_kinds") or "").split(",") if k.strip() in KINDS]
    cats = [c.strip() for c in (s.get("qf_categories") or "").replace("，", ",").split(",") if c.strip()] or [""]
    periods = [p.strip() for p in (s.get("qf_periods") or "7d").split(",") if p.strip()] or ["7d"]
    acc_h, hot_h = float(s.get("qf_account_hours") or 24), float(s.get("qf_hot_hours") or 12)
    out = []
    if s.get("kol_enabled") == "1":   # 达人库:每个采集品类一个任务
        from . import kol
        out += [("buyers", c, "", float(s.get("kol_hours") or 72)) for c in kol.collect_cats()]
    for k in kinds:
        if k in HOT:
            out += [(k, c, p, hot_h) for c in cats for p in periods]
        else:
            out.append((k, "", "", acc_h))
    return out


SHOP_FREE = ("hot_topics", "buyers")   # 不分店铺的数据(平台经典话题):只下发一份,谁有登录谁做


def _task_params(r: dict) -> dict:
    try:
        return json.loads(r["params"] or "{}")
    except Exception:
        return {}


def _has_open_task(kind: str, scope: str, period: str, shop: str = "") -> bool:
    for r in q_all("SELECT params FROM crawl_tasks WHERE type='qianfan' AND query=? AND label=? AND status IN ('pending','running')", kind, scope):
        p = _task_params(r)
        if p.get("period", "") == period and p.get("shop", "") == shop:
            return True
    return False


def add_task(kind: str, scope: str = "", period: str = "", shop: str = "") -> int:
    p = {"period": period, "category": scope, "shop": shop}
    if kind == "buyers":   # 达人采集:每个品类最多翻几页
        try:
            p["pages"] = max(1, min(int(get_settings().get("kol_pages") or 30), 100))
        except ValueError:
            p["pages"] = 30
    params = json.dumps(p, ensure_ascii=False)
    return q_exec("INSERT INTO crawl_tasks(project_id,type,query,lim,purpose,label,params) VALUES(NULL,'qianfan',?,0,'qianfan',?,?)", kind, scope, params).lastrowid


def schedule(force: bool = False, kinds: list[str] | None = None) -> int:
    """没有未完成的同步任务、并且上次同步已经超过间隔的,新建一个任务。每个店铺各一份(平台话题榜只一份)。
    返回新建的个数。force=True(管理端「立即同步」)不看间隔。"""
    s = get_settings()
    if not force and s.get("qf_enabled") != "1":
        return 0
    shop_ids = [r["shop_id"] for r in shops()] or [""]
    n = 0
    for kind, scope, period, hours in _wanted(s):
        if kinds and kind not in kinds:
            continue
        for shop in ([""] if kind in SHOP_FREE else shop_ids):
            if _has_open_task(kind, scope, period, shop):
                continue
            if not force:
                if kind == "buyers":   # 达人库不存快照:看最近一次成功的任务
                    snap = q_one("SELECT finished_at AS snap_at FROM crawl_tasks WHERE type='qianfan' AND query='buyers' AND label=? AND status='done' ORDER BY id DESC LIMIT 1", scope)
                elif kind in SHOP_FREE:   # 不分店铺的数据,快照存在哪个店铺名下都算(谁的采集端做的就记在谁名下)
                    snap = q_one("SELECT snap_at FROM qf_snapshots WHERE kind=? AND scope=? AND period=? ORDER BY id DESC LIMIT 1", kind, scope, period)
                else:
                    snap = q_one("SELECT snap_at FROM qf_snapshots WHERE kind=? AND scope=? AND period=? AND shop_id=? ORDER BY id DESC LIMIT 1", kind, scope, period, shop)
                if snap and q_one("SELECT ? > datetime('now', ?) ok", snap["snap_at"], f"-{int(hours * 60)} minutes")["ok"]:
                    continue  # 还没到下次同步的时间
                fail = q_one("SELECT 1 FROM crawl_tasks WHERE type='qianfan' AND query=? AND label=? AND status='failed' AND finished_at > datetime('now','-1 hours') "
                             "AND (?='' OR params LIKE ?)", kind, scope, shop, f'%"shop": "{shop}"%')
                if fail:
                    continue  # 刚失败过:一小时后再试,不要一直重复下发
            add_task(kind, scope, period, shop)
            n += 1
    return n


# ---------- 示例数据:没接上千帆之前,用来预览页面(带 sample 标记,随时可以清掉) ----------
def load_sample() -> int:
    clear_sample()
    import random
    rnd = random.Random(7)
    base = 3200
    for i in range(14):  # 14 天的账号数据,用来画趋势
        base += rnd.randint(8, 60)
        ingest({"kind": "account", "sample": True, "account": {"name": "示例账号", "fans": base, "new_fans": rnd.randint(5, 60), "notes": 40 + i // 3,
                                                               "views": rnd.randint(8000, 24000), "likes": rnd.randint(300, 900), "collects": rnd.randint(200, 800),
                                                               "comments": rnd.randint(20, 120), "shares": rnd.randint(10, 80), "visits": rnd.randint(200, 900)}})
        q_exec("UPDATE qf_snapshots SET snap_at=datetime('now', ?) WHERE id=(SELECT MAX(id) FROM qf_snapshots WHERE kind='account')", f"-{13 - i} days")
    titles = [d["title"] for d in q_all("SELECT title FROM drafts WHERE title!='' ORDER BY id DESC LIMIT 8")] or ["示例笔记 A", "示例笔记 B", "示例笔记 C"]
    items = [{"note_id": f"sample{i}", "title": t, "published_at": time.strftime("%Y-%m-%d"), "views": rnd.randint(800, 9000), "reads": rnd.randint(500, 6000),
              "likes": rnd.randint(20, 400), "collects": rnd.randint(10, 300), "comments": rnd.randint(0, 60), "shares": rnd.randint(0, 30), "new_fans": rnd.randint(0, 25)}
             for i, t in enumerate(titles)]
    ingest({"kind": "notes", "sample": True, "items": items})
    hot_n = [{"rank": i + 1, "title": t, "author": f"示例博主{i + 1}", "likes": 52000 - i * 3900, "collects": 31000 - i * 2400, "comments": 2100 - i * 150}
             for i, t in enumerate(["空气炸锅这样做早餐,10 分钟搞定", "打工人午餐便当 | 一周不重样", "零失败的一人食晚餐", "懒人电饭煲焖饭合集", "夜宵吃什么不胖", "小个子秋冬通勤穿搭", "平价防晒测评", "租房改造 | 500 元焕新", "周末 citywalk 路线", "新手化妆一步一步来"])]
    ingest({"kind": "hot_notes", "sample": True, "scope": "", "period": "7d", "items": hot_n})
    ingest({"kind": "hot_topics", "sample": True, "scope": "", "period": "7d",
            "items": [{"rank": i + 1, "name": n, "heat": 98000 - i * 7000, "notes": 4200 - i * 300} for i, n in enumerate(["早餐打卡", "一人食", "减脂餐", "便当", "懒人食谱", "秋冬穿搭", "通勤妆", "平价好物", "租房改造", "周末去哪儿"])]})
    ingest({"kind": "hot_keywords", "sample": True, "scope": "", "period": "7d",
            "items": [{"rank": i + 1, "keyword": n, "heat": 120000 - i * 9000} for i, n in enumerate(["空气炸锅食谱", "低卡早餐", "一周便当", "电饭煲焖饭", "减脂晚餐", "早八通勤妆", "防晒怎么选", "小户型收纳", "新手化妆教程", "秋冬显瘦穿搭"])]})
    return 4 + 14


def clear_sample() -> int:
    return q_exec("DELETE FROM qf_snapshots WHERE data LIKE '%\"sample\": true%'").rowcount


def has_sample() -> bool:
    return bool(q_one("SELECT 1 FROM qf_snapshots WHERE data LIKE '%\"sample\": true%' LIMIT 1"))


def hot_names(scope: str = "", limit: int = 12) -> dict:
    """员工界面:选题时参考的热门话题和关键词(取各店铺最新一份,优先当前赛道的榜,没有就用全站;多个店铺的词合并去重)。"""
    out = {"topics": [], "keywords": [], "at": ""}
    for kind, key in (("hot_topics", "topics"), ("hot_keywords", "keywords")):
        names: list[str] = []
        for sh in [""] + [r["shop_id"] for r in shops()]:
            rows = None
            for sc in ([scope] if scope else []) + [""]:
                r = q_one("SELECT snap_at, data FROM qf_snapshots WHERE kind=? AND scope=? AND shop_id=? ORDER BY id DESC LIMIT 1", kind, sc, sh)
                if r:
                    rows = r
                    break
            if rows:
                names += [i["name"] for i in (rows["data"] or {}).get("items", [])[:limit]]
                out["at"] = max(out["at"], rows["snap_at"])
        out[key] = list(dict.fromkeys(names))[:limit]
    return out
