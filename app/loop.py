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
from . import qianfan, titlelab
from .db import conn, get_settings, q_all, q_exec, q_one

COVER_KINDS = {"html": "程序排版(纯文字)", "photo_text": "实拍图 + 排字", "photo": "纯产品图"}


def _cover_kind(d: dict) -> str:
    """封面类型:新稿记在 cover_card.kind 里;老稿按版式推出来(实拍版式 = 实拍图 + 排字,其余 = 纯文字)。"""
    cc = d.get("cover_card") or {}
    if cc.get("kind"):
        return cc["kind"]
    from .covers import kind_of
    return kind_of(cc["layout"]) if cc.get("layout") else ""


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


def edit_info(pe: dict | None, title_sim: float | None) -> dict:
    """发布时对稿子的改动。标题相似度系统自己算;封面 / 正文有没有改,靠登记时勾选(没勾选的按没改算)。
    大改 = 封面和正文都改了,或标题已经面目全非(相似度 < 0.5);小改 = 改了封面或正文之一,或标题有改动(< 0.8)。"""
    pe = pe if isinstance(pe, dict) else {}
    cover, body = bool(pe.get("cover")), bool(pe.get("body"))
    ts = title_sim if title_sim is not None else pe.get("title_sim")
    major = (cover and body) or (ts is not None and ts < 0.5)
    minor = cover or body or (ts is not None and ts < 0.8)
    return {"level": "大改" if major else "小改" if minor else "原样", "title_sim": ts, "cover": cover, "body": body, "src": pe.get("src") or ""}


def snapshot(d: dict, cal: dict | None = None) -> dict:
    """稿子发布时的打分快照:评分(总分 / 等级 / 各维度 / 量化贴合度)、封面类型和版式、标题和正文的写法特征、角度、帖子类型。
    只由稿子自己的字段算出来,和千帆数据无关,所以任何时候都能补算(老稿的快照取的是现在的内容,标 backfill)。"""
    from . import stats
    f = stats.features({"title": d.get("title"), "content": d.get("content"), "tags": d.get("tags"), "images": d.get("images")})
    rv = d.get("review") if isinstance(d.get("review"), dict) else {}
    cc = d.get("cover_card") or {}
    return {"score": rv.get("overall"), "grade": rv.get("grade") or "", "dims": rv.get("dims") or {}, "quant": (rv.get("quant") or {}).get("score"),
            "title_len": f["title_len"], "t_digit": f["t_digit"], "t_mark": f["t_mark"], "t_emoji": f["t_emoji"], "body_len": f["body_len"], "paras": f["paras"],
            "emoji_100": round(f["emoji_100"], 2), "list_ratio": round(f["list_ratio"], 2), "cta": f["cta"], "images": f["images"],
            "cover_kind": _cover_kind(d), "cover_layout": cc.get("layout", ""), "topic": _topic(d.get("angle")), "ptype": d.get("ptype") or "", "at": time.strftime("%Y-%m-%d %H:%M"),
            "title_score": titlelab.title_score(d.get("title") or "", cal)["score"],
            "title_hint": (d.get("checks") or {}).get("title_hint") if isinstance(d.get("checks"), dict) else None}   # 写这篇时有没有带标题写法提示(1 / 0;老稿没有 = None)   # 标题写法分(按千帆真实笔记校准的统计规律,见 titlelab.py)


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
    cal = titlelab.cached(items, at, c["min_days"])
    spawned = {r["spawn_from"]: r["n"] for r in q_all("SELECT spawn_from, COUNT(*) AS n FROM projects WHERE spawn_from IS NOT NULL GROUP BY spawn_from")}
    drafts = q_all("""SELECT d.id, d.title, d.angle, d.account_id, d.account_name, d.project_id, d.published, d.published_at, d.qf_note_id, d.status, d.auto_ok, d.created, d.images, d.cover_card, d.ptype,
        p.track, p.created_by, p.owner_id, p.spawn_from, COALESCE(p.generation, 0) AS generation, d.feat, d.pub_edit
        FROM drafts d LEFT JOIN projects p ON p.id=d.project_id WHERE d.title!='' ORDER BY d.id DESC""")
    need = [d["id"] for d in drafts if (d["published"] or perf.get(d["id"])) and not d["feat"]]   # 已发布但还没存快照的:补存(只取这几篇的正文,不拖慢整个列表)
    if need and register:
        from . import autorun
        for r in autorun._in_chunks("SELECT id, title, content, tags, images, review, angle, ptype, cover_card, checks FROM drafts WHERE id IN ({})", need):
            q_exec("UPDATE drafts SET feat=? WHERE id=?", json.dumps(snapshot(r, cal), ensure_ascii=False), r["id"])
        fresh = {r["id"]: r["feat"] for r in q_all("SELECT id, feat FROM drafts WHERE id IN (%s)" % ",".join(str(i) for i in need))}
        for d in drafts:
            if d["id"] in fresh:
                d["feat"] = fresh[d["id"]]
    rows = []
    for d in drafts:
        it = perf.get(d["id"])
        if it and register and not d["published"]:
            q_exec("UPDATE drafts SET published=1, published_at=? WHERE id=?", it.get("published_at") or time.strftime("%Y-%m-%d"), d["id"])
            d["published"], d["published_at"] = 1, it.get("published_at") or ""
        pe = d["pub_edit"] if isinstance(d["pub_edit"], dict) else {}
        ts = qianfan.title_sim(d["title"], it.get("title") or "") if it else None
        if it and register and pe.get("title_sim") != ts:   # 对上笔记后记下标题改了多少(手动对上时勾选的封面 / 正文改动已经在 pe 里,保留)
            pe = {**pe, "title_sim": ts, "src": pe.get("src") or "auto"}
            q_exec("UPDATE drafts SET pub_edit=? WHERE id=?", json.dumps(pe, ensure_ascii=False), d["id"])
        if it and register and not d["account_id"] and it.get("account_id"):   # 稿子没指定账号、但对上的千帆笔记带账号:账号以笔记的为准(它就是真实发布的账号),补到稿子上
            nm = it.get("account_name") or qianfan.account_name(it["account_id"]) or ""
            q_exec("UPDATE drafts SET account_id=?, account_name=? WHERE id=?", it["account_id"], nm, d["id"])
            d["account_id"], d["account_name"] = it["account_id"], nm
        acc = it.get("account_id") if it else (d["account_id"] or "")
        tier, lift = classify(it, d, base.get(acc or "") or base.get(""), c)
        rows.append({"id": d["id"], "title": d["title"], "topic": _topic(d["angle"]), "angle": d["angle"], "track": d["track"] or "", "project_id": d["project_id"],
                     "account_id": acc or "", "account_name": (it or {}).get("account_name") or d["account_name"] or "", "created_by": d["created_by"] or "", "owner_id": d["owner_id"],
                     "tier": tier, "tier_name": TIERS[tier], "lift": lift, "generation": d["generation"], "spawn_from": d["spawn_from"], "spawned": spawned.get(d["id"], 0),
                     "published": bool(d["published"]), "linked": bool(d["qf_note_id"]), "auto_ok": d["auto_ok"], "cover": (d["images"] or [""])[0] if isinstance(d["images"], list) else "",
                     "published_at": (it or {}).get("published_at") or d["published_at"] or "", "days": _days_since((it or {}).get("published_at") or d["published_at"] or ""),
                     "views": (it or {}).get("views"), "likes": (it or {}).get("likes"), "collects": (it or {}).get("collects"),
                     "comments": (it or {}).get("comments"), "eng": _eng(it) if it else None,
                     "feat": d["feat"] if isinstance(d["feat"], dict) else None, "edit": edit_info(pe, ts) if it else None,
                     "cover_kind": _cover_kind(d), "cover_name": COVER_KINDS.get(_cover_kind(d), "(旧稿没记录)"), "cover_layout": (d["cover_card"] or {}).get("layout", ""), "ptype": d["ptype"] or "(未标注)"})
    return {"rows": rows, "cfg": c, "data_at": at, "title_cal": cal}


def _rate(rs: list[dict]) -> dict:
    mature = [r for r in rs if r["tier"] in ("winner", "good", "normal", "weak")]
    w = [r for r in mature if r["tier"] == "winner"]
    lifts = [r["lift"] for r in mature if r["lift"] is not None]
    return {"n": len(rs), "mature": len(mature), "winners": len(w), "rate": round(len(w) / len(mature), 3) if mature else None,
            "lift_avg": round(sum(lifts) / len(lifts), 2) if lifts else None}


MIN_LEARN = 30   # 有结论的稿子攒到这么多篇,才对「评分准不准 / 什么做法有效」下结论;之前只展示


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = _mean(xs), _mean(ys)
    sx, sy = sum((x - mx) ** 2 for x in xs), sum((y - my) ** 2 for y in ys)
    return None if not sx or not sy else sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy) ** 0.5


def _spearman(xs, ys):
    """秩相关(不受个别爆款的极端值影响)。"""
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i]); r = [0.0] * len(v); i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    return _pearson(ranks(xs), ranks(ys)) if len(xs) >= 3 else None


def _band(v, cuts, labels):
    for c, lab in zip(cuts, labels):
        if v >= c:
            return lab
    return labels[-1]


# 要对比的「做法」:(名称, 把快照变成分组标签的函数)。只放能在出稿前决定的东西
# 第三项:这个做法要「发布时没被改」才能算:cover = 封面没换;title = 标题基本没改(相似度 ≥ 0.8);body = 正文没改;None = 只要不是大改
FACTORS = [
    ("封面类型", lambda f: COVER_KINDS.get(f.get("cover_kind"), "") or "(没记录)", "cover"),
    ("封面版式", lambda f: f.get("cover_layout") or "(没记录)", "cover"),
    ("标题带数字", lambda f: "带数字" if f.get("t_digit") else "不带数字", "title"),
    ("标题带问号 / 感叹号", lambda f: "带" if f.get("t_mark") else "不带", "title"),
    ("标题带 emoji", lambda f: "带" if f.get("t_emoji") else "不带", "title"),
    ("标题字数", lambda f: _band(f.get("title_len") or 0, (19, 13, 0), ("19 字以上", "13–18 字", "12 字以内")), "title"),
    ("正文字数", lambda f: _band(f.get("body_len") or 0, (600, 300, 0), ("600 字以上", "300–599 字", "300 字以内")), "body"),
    ("结尾有互动引导", lambda f: "有" if f.get("cta") else "没有", "body"),
    ("帖子类型", lambda f: f.get("ptype") or "(未标注)", None),
]


def _unedited(r: dict, part) -> bool:
    e = r.get("edit") or {}
    if part == "cover":
        return not e.get("cover")
    if part == "body":
        return not e.get("body")
    if part == "title":
        return e.get("title_sim") is None or e["title_sim"] >= 0.8
    return e.get("level") != "大改"


def learning(ev: dict) -> dict:
    """评分准不准、什么做法有效:拿稿子发布时的打分快照,和后来的真实表现(相对本账号平均的倍数)对比。
    只用发布满 N 天、对上千帆数据的稿子;用「相对本账号平均」比,弱账号的低数据不会拖累结论。有结论的稿子不到 MIN_LEARN 篇时只展示、不下结论。"""
    rows = ev["rows"]
    mature = [r for r in rows if r["tier"] in ("winner", "good", "normal", "weak") and r.get("lift") is not None]
    done_all = [r for r in mature if r.get("feat")]
    done = [r for r in done_all if _unedited(r, None)]   # 评分 / 维度对比:发布时改得面目全非的(大改)不算,表现好坏已经不是我们的评分能解释的
    obs = [r for r in rows if r["tier"] == "observing"]
    min_days = ev["cfg"]["min_days"]
    soon = [max(0, min_days - r["days"]) for r in obs if r.get("days") is not None]

    def agg(rs):
        lifts = [r["lift"] for r in rs]
        return {"n": len(rs), "winners": sum(1 for r in rs if r["tier"] == "winner"), "lift": round(_mean(lifts), 2) if lifts else None}
    bands = []
    scored = [r for r in done if r["feat"].get("score") is not None]
    for lo, hi, lab in ((90, 101, "90 分以上"), (80, 90, "80–89 分"), (70, 80, "70–79 分"), (0, 70, "70 分以下")):
        rs = [r for r in scored if lo <= r["feat"]["score"] < hi]
        if rs:
            bands.append({"label": lab, **agg(rs)})
    dims = []
    from .llm import REVIEW_DIMS
    for dname in REVIEW_DIMS:
        pairs = [(r["feat"]["dims"][dname], r["lift"]) for r in done if isinstance(r["feat"].get("dims"), dict) and r["feat"]["dims"].get(dname) is not None]
        r_ = _pearson([p[0] for p in pairs], [p[1] for p in pairs]) if len(pairs) >= 5 else None
        dims.append({"name": dname, "n": len(pairs), "r": None if r_ is None else round(r_, 2)})
    corr_all = _pearson([r["feat"]["score"] for r in scored], [r["lift"] for r in scored]) if len(scored) >= 5 else None
    factors = []
    for name, fn, part in FACTORS:
        g = defaultdict(list)
        for r in done_all:
            if _unedited(r, part):
                g[fn(r["feat"])].append(r)
        items = sorted(({"label": k, **agg(v)} for k, v in g.items()), key=lambda x: -(x["lift"] or 0))
        if len(items) > 1:   # 只有一种做法没得比,不列
            factors.append({"name": name, "items": items})
    egroups = defaultdict(list)
    for r in mature:
        egroups[(r.get("edit") or {}).get("level") or "原样"].append(r)
    edits = [{"label": k, **agg(egroups[k])} for k in ("原样", "小改", "大改") if egroups.get(k)] if len(egroups) > 1 else []
    # 标题写法分(按千帆真实笔记校准的)和我们自己稿子的真实表现对不对得上:标题基本没改的稿子,写法分和表现的排名相关
    cal = ev.get("title_cal")
    tr = [(titlelab.title_score(r["title"], cal)["score"], r["lift"]) for r in mature if cal and _unedited(r, "title") and r.get("title")]
    tr = [(a, b) for a, b in tr if a is not None]
    title_check = {"n": len(tr), "corr": round(_spearman([x[0] for x in tr], [x[1] for x in tr]) or 0, 2) if len(tr) >= 8 and _spearman([x[0] for x in tr], [x[1] for x in tr]) is not None else None}
    # 标题写法提示有没有用:接了提示的稿子 vs 对照组(每 5 篇留 1 篇不带),标题发布时没被改过的才算
    hg = defaultdict(list)
    for r in done_all:
        h = (r.get("feat") or {}).get("title_hint")
        if h in (0, 1) and _unedited(r, "title"):
            hg["带提示" if h else "没带提示(对照)"].append(r)
    hint_effect = [{"label": k, **agg(hg[k])} for k in ("带提示", "没带提示(对照)") if hg.get(k)] if len(hg) > 1 else []
    return {"hint_effect": hint_effect, "title_check": title_check, "n_edited": len(done_all) - len(done), "edits": edits, "n_done": len(done), "n_obs": len(obs), "need": MIN_LEARN, "ready": len(done) >= MIN_LEARN, "soon_days": min(soon) if soon else None,
            "lift_avg": round(_mean([r["lift"] for r in done]), 2) if done else None, "bands": bands, "score_corr": None if corr_all is None else round(corr_all, 2),
            "dims": dims if scored else [], "factors": factors, "min_days": min_days}


def _title_lab(ev: dict) -> dict | None:
    """标题写法校准结果(来自千帆全部真实笔记)+ 我们已生成的稿子里,各种写法占多少(提醒我们常用的写法是有利还是不利)。"""
    cal = ev.get("title_cal")
    if not cal:
        return None
    titles = [r["title"] for r in ev["rows"] if r.get("title")]
    share = {}
    if titles:
        feats = [titlelab.title_features(t) for t in titles]
        share = {k: round(sum(1 for f in feats if f[k]) / len(feats), 2) for k, *_ in titlelab.FEATURES}
    return {**cal, "ours": {"n": len(titles), "share": share}}


def adherence(ev: dict) -> list[dict]:
    """每个人发出去的稿子有多少能对上笔记、对上的里面有多少是原样发的(发布一致率):对不上的追踪不到,改得多的归因不了。"""
    by = defaultdict(lambda: {"published": 0, "linked": 0, "original": 0, "edited": 0})
    for r in ev["rows"]:
        if not (r["published"] or r.get("edit")):
            continue
        b = by[r.get("created_by") or "(未记录)"]
        b["published"] += 1
        if r.get("edit"):
            b["linked"] += 1
            b["original" if r["edit"]["level"] == "原样" else "edited"] += 1
    out = [{"name": k, **v, "link_rate": round(v["linked"] / v["published"], 2) if v["published"] else None,
            "original_rate": round(v["original"] / v["linked"], 2) if v["linked"] else None} for k, v in by.items()]
    return sorted(out, key=lambda x: -x["published"])


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
            "by_topic": group("topic"), "by_cover": group("cover_name", 1), "by_ptype": group("ptype", 1), "by_account": group("account_name", 1), "learn": learning(ev), "adherence": adherence(ev), "title_lab": _title_lab(ev), "by_project": by_project, "spawn_effect": gens, "cfg": ev["cfg"], "data_at": ev["data_at"],
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
        # 继续复刻出来的任务沿用原任务的归属(自动复刻也一样)
        pid = cn.execute("INSERT INTO projects(track,keywords,direction,product_id,account_id,account_name,angle_pref,account_style,created_by,spawn_from,generation,owner_id) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                         (p.get("track") or "", json.dumps(p.get("keywords") or [], ensure_ascii=False), direction, p.get("product_id"),
                          d["account_id"] or p.get("account_id") or "", d["account_name"] or p.get("account_name") or "", p.get("angle_pref") or "",
                          p.get("account_style") or "", by, did, (row.get("generation") or 0) + 1, p.get("owner_id"))).lastrowid
        for i in refs:
            cn.execute("INSERT OR IGNORE INTO project_notes(project_id,note_id) VALUES(?,?)", (pid, i))
        pb = cn.execute("INSERT INTO playbooks(project_id,name,note_ids,kind) VALUES(?,?,?,?)", (pid, f"爆款复刻·{d['title'][:10]}", json.dumps(refs), d.get("ptype") or "")).lastrowid   # 沿用爆款的帖子类型
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
