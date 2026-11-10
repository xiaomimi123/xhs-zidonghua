"""闭环:批量复刻 → 发布测试 → 数据回流 → 判定哪些稿子爆了 → 以爆款为样本继续复刻。

- evaluate():把我们的稿子和千帆里的笔记表现对上号,按「相对本账号平均」判定爆款;顺手把对上号的稿子登记成已发布。
- overview():漏斗(生成→合格→发布→有数据→爆款)、爆款率、按任务批次 / 主题 / 账号的爆款率、继续复刻是否更容易出爆款。
- spawn():把一篇爆款稿登记进笔记库当样本,新建一个任务走完整流水线(拆解 → 规划角度 → 写稿 → 配图 → 质检),每次复刻代数 +1。
- auto_tick():打开「自动继续复刻」后,新出现的爆款会自动复刻(每天有上限,默认关闭)。
"""
import datetime
import json
import re
import time
from collections import defaultdict
from . import qianfan
from .db import conn, get_settings, q_all, q_exec, q_one

TIERS = {"winner": "爆款", "good": "不错", "normal": "一般", "weak": "偏弱", "observing": "观察中", "nodata": "没对上数据", "unpublished": "未发布"}
_auto_at = 0.0


def cfg() -> dict:
    s = get_settings()

    def num(k, d, cast=int):
        try:
            return cast(float(s.get(k) or d))
        except (TypeError, ValueError):
            return cast(d)
    return {"win_lift": num("loop_win_lift", 2.0, float), "min_eng": num("loop_min_eng", 20), "min_days": num("loop_min_days", 3),
            "spawn_n": max(1, min(num("loop_spawn_count", 5), 20)), "daily_cap": num("loop_daily_cap", 10), "auto": s.get("loop_auto") == "1"}


def _eng(r: dict) -> int:
    return (r.get("likes") or 0) + (r.get("collects") or 0) + (r.get("comments") or 0)


def _baselines(items: list[dict]) -> dict[str, tuple[str, float]]:
    """每个账号的对比基线:该账号所有笔记的互动(赞+藏+评)去掉最高 5% 后的平均;互动太少没有区分度的账号改用阅读量。"""
    by: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by[it.get("account_id") or ""].append(it)
    out = {}
    for a, rows in by.items():
        e = [_eng(r) for r in rows]
        metric = "eng" if sum(e) / len(e) >= 2 else "views"
        out[a] = (metric, qianfan._trimmed_mean(e if metric == "eng" else [r.get("views") or 0 for r in rows]))
    return out


def _days_since(day: str) -> int | None:
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", day or "")
    if not m:
        return None
    try:
        return (datetime.date.today() - datetime.date(*map(int, m.groups()))).days
    except ValueError:
        return None


def _topic(angle: str) -> str:
    """角度文字「主题:xxx｜角度:…」或「黄金奶做法:写给…」取冒号前面的主题。"""
    t = re.split("[:：｜|]", (angle or "").replace("主题:", "").replace("主题：", ""), maxsplit=1)[0].strip()
    return t[:20]


def classify(it: dict | None, d: dict, base: tuple[str, float] | None, c: dict) -> tuple[str, float | None]:
    if not it:
        return ("nodata" if d["published"] else "unpublished"), None
    metric, b = base or ("eng", 0.0)
    value = _eng(it) if metric == "eng" else (it.get("views") or 0)
    lift = round(value / b, 2) if b else None
    age = _days_since(it.get("published_at") or d["published_at"] or "")
    if age is not None and age < c["min_days"]:
        return "observing", lift
    eng = _eng(it)
    if eng >= c["min_eng"] and (lift is None or lift >= c["win_lift"]):
        return "winner", lift
    if lift is not None and lift >= 1.3 and eng >= max(3, c["min_eng"] // 4):
        return "good", lift
    if lift is not None and lift < 0.7:
        return "weak", lift
    return "normal", lift


def evaluate(register: bool = True, pre: tuple | None = None) -> dict:
    """返回 {"rows": [每篇稿子的判定], "cfg": 当前阈值}。register=True 时把对上号的稿子登记成已发布。
    pre = (笔记列表, 数据时间, 对号结果):调用方已经算过就传进来,不再重算(成品库每次打开都要用)。"""
    c = cfg()
    if pre:
        items, at, perf = pre
    else:
        items, at = qianfan.notes_all()
        perf = qianfan.match_drafts(items)
    base = _baselines(items)
    spawned = {r["spawn_from"]: r["n"] for r in q_all("SELECT spawn_from, COUNT(*) AS n FROM projects WHERE spawn_from IS NOT NULL GROUP BY spawn_from")}
    drafts = q_all("""SELECT d.id, d.title, d.angle, d.account_id, d.account_name, d.project_id, d.published, d.published_at, d.qf_note_id, d.status, d.auto_ok, d.created, d.images,
        p.track, p.created_by, p.spawn_from, COALESCE(p.generation, 0) AS generation
        FROM drafts d LEFT JOIN projects p ON p.id=d.project_id WHERE d.title!='' ORDER BY d.id DESC""")
    rows = []
    for d in drafts:
        it = perf.get(d["id"])
        if it and register and not d["published"]:
            q_exec("UPDATE drafts SET published=1, published_at=? WHERE id=?", it.get("published_at") or time.strftime("%Y-%m-%d"), d["id"])
            d["published"], d["published_at"] = 1, it.get("published_at") or ""
        acc = it.get("account_id") if it else (d["account_id"] or "")
        tier, lift = classify(it, d, base.get(acc or "") or base.get(""), c)
        rows.append({"id": d["id"], "title": d["title"], "topic": _topic(d["angle"]), "angle": d["angle"], "track": d["track"] or "", "project_id": d["project_id"],
                     "account_id": acc or "", "account_name": (it or {}).get("account_name") or d["account_name"] or "", "created_by": d["created_by"] or "",
                     "tier": tier, "tier_name": TIERS[tier], "lift": lift, "generation": d["generation"], "spawn_from": d["spawn_from"], "spawned": spawned.get(d["id"], 0),
                     "published": bool(d["published"]), "linked": bool(d["qf_note_id"]), "auto_ok": d["auto_ok"], "cover": (d["images"] or [""])[0] if isinstance(d["images"], list) else "",
                     "published_at": (it or {}).get("published_at") or d["published_at"] or "", "days": _days_since((it or {}).get("published_at") or d["published_at"] or ""),
                     "views": (it or {}).get("views"), "likes": (it or {}).get("likes"), "collects": (it or {}).get("collects"),
                     "comments": (it or {}).get("comments"), "eng": _eng(it) if it else None})
    return {"rows": rows, "cfg": c, "data_at": at}


def _rate(rs: list[dict]) -> dict:
    mature = [r for r in rs if r["tier"] in ("winner", "good", "normal", "weak")]
    w = [r for r in mature if r["tier"] == "winner"]
    lifts = [r["lift"] for r in mature if r["lift"] is not None]
    return {"n": len(rs), "mature": len(mature), "winners": len(w), "rate": round(len(w) / len(mature), 3) if mature else None,
            "lift_avg": round(sum(lifts) / len(lifts), 2) if lifts else None}


def overview(ev: dict | None = None) -> dict:
    ev = ev or evaluate()
    rows = ev["rows"]
    pub = [r for r in rows if r["published"] or r["tier"] not in ("unpublished", "nodata")]
    got = [r for r in pub if r["tier"] not in ("unpublished", "nodata")]
    funnel = [("generated", "已生成", len(rows)), ("qualified", "质检合格", sum(1 for r in rows if r["auto_ok"] == 1)), ("published", "已发布", len(pub)),
              ("data", "数据已回流", len(got)), ("mature", "数据够久", sum(1 for r in got if r["tier"] != "observing")), ("winner", "爆款", sum(1 for r in got if r["tier"] == "winner"))]

    def group(key, minimum=2):
        g = defaultdict(list)
        for r in got:
            g[r[key] or "(未标注)"].append(r)
        out = [{"name": k, **_rate(v)} for k, v in g.items()]
        out = [x for x in out if x["mature"] >= minimum or x["winners"]]
        out.sort(key=lambda x: (x["rate"] or 0, x["winners"], x["mature"]), reverse=True)
        return out
    projects = defaultdict(list)
    for r in got:
        projects[r["project_id"]].append(r)
    names = {p["id"]: p for p in q_all("SELECT id, track, keywords, created FROM projects WHERE id IN (%s)" % (",".join(str(i) for i in projects if i) or "0"))}
    by_project = sorted(({"project_id": pid, "name": (names.get(pid) or {}).get("track", "") + "·" + "、".join(((names.get(pid) or {}).get("keywords") or [])[:2]),
                          "created": ((names.get(pid) or {}).get("created") or "")[:10], **_rate(v)} for pid, v in projects.items()),
                         key=lambda x: x["created"], reverse=True)[:12]
    gens = {"original": _rate([r for r in got if not r["generation"]]), "spawned": _rate([r for r in got if r["generation"]])}
    return {"funnel": [{"key": k, "name": n, "n": v} for k, n, v in funnel], "tiers": {t: sum(1 for r in rows if r["tier"] == t) for t in TIERS},
            "by_topic": group("topic"), "by_account": group("account_name", 1), "by_project": by_project, "spawn_effect": gens, "cfg": ev["cfg"], "data_at": ev["data_at"],
            "no_data": not got and not q_one("SELECT 1 AS x FROM qf_snapshots WHERE kind='notes' LIMIT 1")}


def _spawn_busy(did: int) -> bool:
    return bool(q_one("""SELECT 1 AS x FROM projects p JOIN autoruns a ON a.project_id=p.id
        WHERE p.spawn_from=? AND a.status IN ('profiling','collecting','tagging','analyzing','writing','retrying')""", did))


def spawn(did: int, count: int | None = None, by: str = "") -> int:
    """以一篇爆款稿为样本继续复刻,返回新任务(项目)id。"""
    from . import autorun, worker
    d = q_one("SELECT * FROM drafts WHERE id=?", did)
    if not d or not d["title"] or not d["content"]:
        raise ValueError("这篇稿子还没有内容")
    if _spawn_busy(did):
        raise ValueError("这篇稿子正在继续复刻中,等这一轮跑完再来")
    c = cfg()
    count = max(1, min(int(count or c["spawn_n"]), 20))
    p = q_one("SELECT * FROM projects WHERE id=?", d["project_id"]) or {}
    row = next((r for r in evaluate(register=False)["rows"] if r["id"] == did), {})
    # 1 把这篇稿子登记成笔记库里的一篇样本(带上真实表现数据),拆解时和普通笔记一样用
    uid = f"ours:{did}"
    vals = (d["title"], d["content"], row.get("account_name") or "我们", row.get("likes") or 0, row.get("collects") or 0, row.get("comments") or 0,
            json.dumps(d["tags"] or [], ensure_ascii=False), p.get("track") or "", ((p.get("keywords") or [""])[0] if p.get("keywords") else ""))
    with conn() as cn:
        cn.execute("INSERT OR IGNORE INTO notes(uid,url,title,content,author,likes,collects,comments,tags,note_type,track,keyword,source,ai_status,topic) "
                   "VALUES(?,?,?,?,?,?,?,?,?,'image',?,?,'ours','done',?)", (uid, uid, *vals[:3], *vals[3:6], vals[6], vals[7], vals[8], row.get("topic") or ""))
        cn.execute("UPDATE notes SET title=?, content=?, likes=?, collects=?, comments=? WHERE uid=?", (d["title"], d["content"], *vals[3:6], uid))
        nid = cn.execute("SELECT id FROM notes WHERE uid=?", (uid,)).fetchone()[0]
        refs = [nid]
        old = cn.execute("SELECT note_ids FROM playbooks WHERE id=?", (d["playbook_id"],)).fetchone() if d["playbook_id"] else None
        if old:  # 这篇当初是照哪些爆款笔记复刻的,带上最多 2 篇原样本,让拆解卡更完整
            refs += [i for i in json.loads(old[0] or "[]") if i != nid][:2]
        direction = (f"以我们已发布并验证过的爆款《{d['title']}》为样本:保留它成功的标题句式、开头钩子、正文结构和语气,"
                     f"换新的主题细节、场景、搭配,不要照抄原文。" + (f"原内容方向:{p['direction']}" if p.get("direction") else ""))
        pid = cn.execute("INSERT INTO projects(track,keywords,direction,product_id,account_id,account_name,angle_pref,account_style,created_by,spawn_from,generation) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (p.get("track") or "", json.dumps(p.get("keywords") or [], ensure_ascii=False), direction, p.get("product_id"),
                          d["account_id"] or p.get("account_id") or "", d["account_name"] or p.get("account_name") or "", p.get("angle_pref") or "",
                          p.get("account_style") or "", by, did, (row.get("generation") or 0) + 1)).lastrowid
        for i in refs:
            cn.execute("INSERT OR IGNORE INTO project_notes(project_id,note_id) VALUES(?,?)", (pid, i))
        pb = cn.execute("INSERT INTO playbooks(project_id,name,note_ids) VALUES(?,?,?)", (pid, f"爆款复刻·{d['title'][:10]}", json.dumps(refs))).lastrowid
    worker.submit_playbook(pb)
    autorun.new_run(pid, count, "analyzing", f"正在拆解爆款《{d['title'][:14]}》", pbs=[pb], refs=len(refs), reused=0, spawn_from=did)
    return pid


def _spawned_today() -> int:
    return q_one("SELECT COALESCE(SUM(a.target),0) AS n FROM projects p JOIN autoruns a ON a.project_id=p.id "
                 "WHERE p.spawn_from IS NOT NULL AND date(a.created,'localtime')=date('now','localtime')")["n"]


def auto_tick(force: bool = False) -> int:
    """打开「自动继续复刻」后:新出现的爆款(还没复刻过的)自动复刻,每天最多 daily_cap 篇。返回本次新开的任务数。"""
    global _auto_at
    c = cfg()
    if not c["auto"] or not (get_settings().get("deepseek_api_key") or "").strip():
        return 0
    if not force and time.time() - _auto_at < 600:
        return 0
    _auto_at = time.time()
    left, started = c["daily_cap"] - _spawned_today(), 0
    for r in evaluate()["rows"]:
        if left <= 0:
            break
        if r["tier"] == "winner" and not r["spawned"] and not r["generation"] >= 3:   # 最多连续复刻 3 代,防止越复刻越走样
            n = min(c["spawn_n"], left)
            try:
                spawn(r["id"], n, by="自动")
            except ValueError:
                continue
            left -= n
            started += 1
    return started
