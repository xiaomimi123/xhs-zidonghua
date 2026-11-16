"""标题写法校准:拿千帆里所有真实笔记(不管是不是我们生成的)的标题和表现,看哪些写法更容易有好表现。

- 表现 = 相对本账号平均的倍数,再除以「同样发布天数的笔记通常的水平」(和复盘一致;弱账号的低数据、新旧笔记互动积累不同都不拖累结论);只用发布满 N 天的。
- 表现分布极度偏斜(少数爆款把平均数拉得很高),所以不比平均数,比「排名」:Mann-Whitney 检验,
  给出「有这种写法的笔记排在没有的前面的概率」(AUC,0.5 = 没差别)和 z 值。
- 一次比十几个写法,偶然碰出 |z| 大于 2 的概率不低,所以 |z| ≥ 2.5 才叫「明显」,2 ~ 2.5 只叫「有倾向」。
- 只是统计相关,不代表因果;样本少的(任一边不足 15 篇)不下结论。
"""
from __future__ import annotations

import datetime
import math
import re
from statistics import median

import json
import threading
import time

from . import stats
from .db import get_settings, q_all, q_exec

MIN_SIDE = 15      # 有 / 没有这种写法的笔记,任一边少于这个数就不下结论
Z_SIG, Z_LEAN = 2.5, 2.0

# (key, 名称, 说明, 判断函数);只放标题本身就能判断、写稿时能控制的写法
FEATURES = [
    ("digit", "标题带数字", "如「3 个」「5 分钟」", lambda t, f: bool(f["t_digit"])),
    ("mark", "标题带问号 / 感叹号", "", lambda t, f: bool(f["t_mark"])),
    ("question", "标题含疑问词", "怎么 / 为什么 / 吗 / ?", lambda t, f: bool(re.search(r"怎么|为什么|吗|[？?]", t))),
    ("emoji", "标题带 emoji", "", lambda t, f: bool(f["t_emoji"])),
    ("long", "标题 19 字以上", "", lambda t, f: f["title_len"] >= 19),
    ("short", "标题 12 字以内", "", lambda t, f: f["title_len"] <= 12),
    ("sep", "标题带「｜」「【」", "分段 / 加标签的写法", lambda t, f: bool(re.search(r"[｜|【\[]", t))),
    ("me", "标题含「我」", "第一人称", lambda t, f: "我" in t),
    ("you", "标题称呼读者", "你 / 姐妹 / 宝子 / 家人们", lambda t, f: bool(re.search(r"你|姐妹|宝子|家人们", t))),
    ("hook", "标题用夸张钩子词", "求 / 谁懂 / 救命 / 绝了 / 封神", lambda t, f: bool(re.search(r"求|谁懂|救命|绝了|封神", t))),
    ("review", "标题是测评 / 避雷类", "实测 / 测评 / 避雷 / 踩雷 / 红黑榜", lambda t, f: bool(re.search(r"实测|测评|避雷|踩雷|红黑榜", t))),
    ("price", "标题带价格", "12.8 元 / ¥", lambda t, f: bool(re.search(r"\d+(\.\d+)?\s*(元|块|¥|💰)|¥", t))),
]


def title_features(title: str) -> dict[str, bool]:
    f = stats.features({"title": title, "content": "", "tags": [], "images": []})
    return {k: bool(fn(title or "", f)) for k, _, _, fn in FEATURES}


def mwu(a: list[float], b: list[float]) -> tuple[float, float]:
    """Mann-Whitney:返回 (AUC, z)。AUC = 从 a 里随机抽一个比从 b 里随机抽一个大的概率(相同算一半)。带并列的方差修正。"""
    na, nb = len(a), len(b)
    allv = sorted([(x, 0) for x in a] + [(x, 1) for x in b])
    ranks, i, tie_term = [0.0] * len(allv), 0, 0.0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        for k in range(i, j + 1):
            ranks[k] = (i + j) / 2 + 1
        t = j - i + 1
        tie_term += t ** 3 - t
        i = j + 1
    ra = sum(ranks[k] for k in range(len(allv)) if allv[k][1] == 0)
    u = ra - na * (na + 1) / 2
    n = na + nb
    var = na * nb / 12 * ((n + 1) - tie_term / (n * (n - 1))) if n > 1 else 0
    return u / (na * nb), ((u - na * nb / 2) / math.sqrt(var) if var > 0 else 0.0)


def note_lifts(items: list[dict], min_days: int = 3, today: datetime.date | None = None) -> list[tuple[str, float]]:
    """[(标题, 相对本账号平均的倍数)]:只算发布满 min_days 天、账号有可比基线的笔记。"""
    from . import loop
    today = today or datetime.date.today()
    base = loop._baselines(items)
    fac = loop.age_factors(items, today)   # 发布天数不同的笔记不能直接比(新笔记互动还没攒起来),除以同龄笔记的通常水平
    out = []
    for it in items:
        title = (it.get("title") or "").strip()
        try:
            age = (today - datetime.date.fromisoformat(str(it.get("published_at") or "")[:10])).days
        except ValueError:
            continue
        b = base.get(it.get("account_id") or "") or base.get("")
        if not title or age < min_days or not b or not b[1]:
            continue
        v = loop._eng(it) if b[0] == "eng" else (it.get("views") or 0)
        out.append((title, v / b[1] / loop.age_factor(age, fac)))
    return out


def verdict(auc: float, z: float, n_with: int, n_without: int) -> str:
    if min(n_with, n_without) < MIN_SIDE:
        return "few"
    d = "pos" if z > 0 else "neg"
    return f"sig_{d}" if abs(z) >= Z_SIG else f"lean_{d}" if abs(z) >= Z_LEAN else "none"


def calibrate(items: list[dict], min_days: int = 3, today: datetime.date | None = None) -> dict:
    data = note_lifts(items, min_days, today)
    feats = [(t, l, title_features(t)) for t, l in data]
    rows = []
    for key, name, hint, _ in FEATURES:
        a = [l for t, l, f in feats if f[key]]
        b = [l for t, l, f in feats if not f[key]]
        if not a or not b:
            rows.append({"key": key, "name": name, "hint": hint, "n_with": len(a), "n_without": len(b), "med_with": None, "med_without": None, "auc": None, "z": None, "verdict": "few"})
            continue
        auc, z = mwu(a, b)
        rows.append({"key": key, "name": name, "hint": hint, "n_with": len(a), "n_without": len(b), "med_with": round(median(a), 2), "med_without": round(median(b), 2),
                     "auc": round(auc, 2), "z": round(z, 2), "verdict": verdict(auc, z, len(a), len(b))})
    rows.sort(key=lambda r: (r["verdict"] == "few", -abs(r["z"] or 0)))
    return {"n_notes": len(items), "n_used": len(data), "min_days": min_days, "rows": rows}


def title_score(title: str, cal: dict | None) -> dict:
    """标题写法分(0-100,50 = 没有任何明显规律):只用「明显」和「有倾向」的写法 —— 有这种写法就按它的 AUC 偏离 0.5 加 / 减分,
    没有就反过来(「明显」的满权重,「有倾向」的半权重)。只反映标题写法和表现的统计关系,不是对内容好坏的评价。"""
    if not cal:
        return {"score": None, "hits": []}
    f = title_features(title)
    score, hits = 50.0, []
    for r in cal["rows"]:
        if r["verdict"] not in ("sig_pos", "sig_neg", "lean_pos", "lean_neg") or r["auc"] is None:
            continue
        w = (r["auc"] - 0.5) * 100 * (1.0 if r["verdict"].startswith("sig") else 0.5)
        d = w if f[r["key"]] else -w
        score += d
        hits.append({"name": r["name"], "has": f[r["key"]], "delta": round(d, 1)})
    return {"score": round(max(5.0, min(95.0, score))), "hits": hits}


# 每种写法对应的提示语:(这种写法更好时怎么说, 这种写法更差时怎么说)
ADVICE = {
    "digit": ("标题里带具体数字", "标题里不要硬塞数字"),
    "mark": ("标题可以带问号 / 感叹号", "标题少用问号和感叹号,用陈述句把答案或亮点直接写出来"),
    "question": ("标题可以用提问的写法", "标题不要用「怎么 / 为什么 / 吗」这类提问,直接说结论"),
    "emoji": ("标题里加一个贴切的 emoji", "标题不要加 emoji"),
    "long": ("标题写满 19~20 字,把对象和亮点说清楚", "标题别写太长"),
    "short": ("标题写得短一些(12 字以内)", "标题别写得太短(12 字以内偏弱),把对象和亮点说清楚"),
    "sep": ("标题可以用「｜」把关键词和亮点分成两段", "标题不要用「｜」「【】」"),
    "me": ("标题可以用第一人称「我」", "标题少用「我」"),
    "you": ("标题可以直接称呼读者(你 / 姐妹)", "标题少用「你 / 姐妹」这类称呼"),
    "hook": ("标题可以用「谁懂 / 绝了」这类钩子词", "标题不要用「求 / 谁懂 / 救命 / 绝了」这类夸张钩子词"),
    "review": ("标题可以用测评 / 避雷的角度", "标题少用测评 / 避雷这类写法"),
    "price": ("标题可以带价格", "标题不要带价格"),
}
HINT_MIN_SIDE = 30        # 写进提示词的规律,有 / 没有两边都要至少这么多篇笔记(比「展示」的门槛 15 更严)
HINT_CONTROL_EVERY = 5    # 每 5 篇留 1 篇不带这段提示(对照组),之后才知道「接了提示」到底有没有用


def prompt_hint(cal: dict | None) -> str:
    """只用「明显」(sig_*)且样本够多的写法;有倾向的、样本少的不进提示词。没有符合的返回空字符串。"""
    if not cal:
        return ""
    do, avoid = [], []
    for r in cal["rows"]:
        if r["verdict"] not in ("sig_pos", "sig_neg") or min(r["n_with"], r["n_without"]) < HINT_MIN_SIDE or r["key"] not in ADVICE:
            continue
        (do if r["verdict"] == "sig_pos" else avoid).append(ADVICE[r["key"]][0 if r["verdict"] == "sig_pos" else 1])
    if not do and not avoid:
        return ""
    lines = [f"【标题写法参考】来自千帆 {cal['n_used']} 篇真实笔记的统计(表现 = 相对本账号平均,已按发布天数校正),只是倾向,真实、合规和其他要求优先,不要为了凑写法写得生硬:"]
    if do:
        lines.append("- 数据里表现更好的写法:" + ";".join(do))
    if avoid:
        lines.append("- 数据里表现偏差的写法:" + ";".join(avoid))
    lines.append("- 主标题和备选标题都照这个来,但几篇之间句式要有变化,别写成一个模子。")
    return "\n".join(lines)


def is_control(draft_id: int) -> bool:
    """对照组:每 HINT_CONTROL_EVERY 篇有 1 篇不给提示(按稿子编号定,稳定可复现)。"""
    return int(draft_id) % HINT_CONTROL_EVERY == 0


# ---------------- 按「标题结构」看(大模型给标题分类,再在真实数据里比哪种结构更吸引人)----------------
STRUCT_HINTS = {
    "场景痛点结果": "点明人群 / 场景 + 痛点 + 结果(如「早八来不及吃早饭?5 分钟一碗热乎的」)",
    "数字清单": "用具体数字组织(几步、几分钟、几点)给出结果或清单",
    "认知反差": "认知反差(以为……其实……)",
    "悬念好奇": "点出结果、把关键办法留在正文里的悬念写法",
    "避坑挑选": "避坑 / 挑选的写法",
    "画面情绪": "具体生活画面 + 情绪",
}
FEAT_NAMES = {"audience": ("标题指向明确的人群 / 场景", "上班族、宝妈、早八、宿舍……"), "specific": ("标题有具体信息", "数字、食材 / 品名、具体场景"),
              "benefit": ("标题有明确的利益点 / 结果", "省时间、更好吃、不踩雷"), "gap": ("标题留有好奇缺口", "读完还想知道答案"),
              "keyword": ("标题带读者会搜的词", "品类词 / 产品名 / 吃法"), "fluff": ("标题主要靠空泛夸词", "绝了 / 封神 / 太好吃了,缺实际信息")}


def title_key(title: str) -> str:
    from . import qianfan
    return qianfan._norm_title(title)


def tag_map() -> dict[str, dict]:
    """{title_key: {"struct": 结构类型, audience/specific/benefit/gap/keyword/fluff: bool}}"""
    out = {}
    for r in q_all("SELECT title_key, struct, dims FROM title_struct"):
        try:
            d = json.loads(r["dims"] or "{}")
        except ValueError:
            d = {}
        out[r["title_key"]] = {"struct": r["struct"], **d}
    return out


def untagged(titles: list[str], tags: dict | None = None) -> list[tuple[str, str]]:
    """[(title_key, 标题)]:还没分类的(同一个标题只算一次)。"""
    tags = tags if tags is not None else tag_map()
    seen, out = set(), []
    for t in titles:
        t = (t or "").strip()
        k = title_key(t)
        if t and k and k not in tags and k not in seen:
            seen.add(k)
            out.append((k, t))
    return out


_tag_state: dict = {"running": False, "done": 0, "total": 0, "error": "", "last": 0.0}
_tag_lock = threading.Lock()


def tag_now(titles: list[str], batch: int = 30, fn=None) -> int:
    """把还没分类的标题交给大模型分类并存起来;返回这次分类了几个。fn 默认 llm.tag_titles(测试用假的替换)。"""
    from . import llm
    fn = fn or llm.tag_titles
    todo = untagged(titles)
    _tag_state.update(done=0, total=len(todo))
    n = 0
    for i in range(0, len(todo), batch):
        part = todo[i:i + batch]
        got = fn([{"id": j, "title": t} for j, (k, t) in enumerate(part)])
        for j, (k, t) in enumerate(part):
            v = got.get(j)
            if v:
                q_exec("INSERT OR REPLACE INTO title_struct(title_key, title, struct, dims) VALUES(?,?,?,?)", k, t[:200], v["struct"],
                       json.dumps({x: bool(v.get(x)) for x in llm.TITLE_FEATS}))
                n += 1
        _tag_state["done"] = min(i + batch, len(todo))
    return n


def tagging_status(titles: list[str] | None = None) -> dict:
    return {**{k: _tag_state[k] for k in ("running", "done", "total", "error")}, "untagged": len(untagged(titles)) if titles is not None else None}


def maybe_start_tagging(titles: list[str], min_gap: float = 600.0, sync: bool = False) -> bool:
    """有没分类的标题、配了大模型 Key、没在跑、距离上次尝试够久,就在后台开始分类。返回是否开始了。sync=True 时在当前线程跑完(测试用)。"""
    with _tag_lock:
        if _tag_state["running"] or time.time() - _tag_state["last"] < min_gap or not (get_settings().get("deepseek_api_key") or "").strip():
            return False
        if not untagged(titles):
            return False
        _tag_state.update(running=True, last=time.time(), error="")

    def run():
        try:
            tag_now(titles)
        except Exception as e:   # 分类失败不能影响页面;下次再试
            _tag_state["error"] = str(e)[:200]
        finally:
            _tag_state["running"] = False
    if sync:
        run()
    else:
        threading.Thread(target=run, daemon=True, name="title-tag").start()
    return True


def calibrate_struct(items: list[dict], tags: dict, min_days: int = 3, today=None) -> dict:
    """按结构校准:每种结构(含「有明确结构」)和每个结构特征,有 / 没有这种写法的笔记谁排名更靠前(表现已按发布天数校正)。只用已分类的标题。"""
    from . import llm
    data = [(l, tags[title_key(t)]) for t, l in note_lifts(items, min_days, today) if title_key(t) in tags]
    groups = [("struct", "has_struct", "有明确的标题结构(不是「其他」)", "", lambda tg: tg["struct"] != "其他")]
    groups += [("struct", s, s, STRUCT_HINTS.get(s, ""), (lambda tg, s=s: tg["struct"] == s)) for s in llm.TITLE_STRUCTS if s != "其他"]
    groups += [("feat", k, FEAT_NAMES[k][0], FEAT_NAMES[k][1], (lambda tg, k=k: bool(tg.get(k)))) for k in llm.TITLE_FEATS]
    rows = []
    for kind, key, name, hint, fn in groups:
        a = [l for l, tg in data if fn(tg)]
        b = [l for l, tg in data if not fn(tg)]
        row = {"group": kind, "key": key, "name": name, "hint": hint, "n_with": len(a), "n_without": len(b), "med_with": None, "med_without": None, "auc": None, "z": None, "verdict": "few"}
        if a and b:
            auc, z = mwu(a, b)
            row.update(med_with=round(median(a), 2), med_without=round(median(b), 2), auc=round(auc, 2), z=round(z, 2), verdict=verdict(auc, z, len(a), len(b)))
        rows.append(row)
    rows.sort(key=lambda r: (r["verdict"] == "few", -abs(r["z"] or 0)))
    return {"n_notes": len(items), "n_used": len(data), "min_days": min_days, "rows": rows}


def struct_hint(cal: dict | None) -> str:
    """只在证据够强时给提示:某种结构明显更好(两边都 ≥ 30 篇)、或「主要靠空泛夸词」明显更差。没有就返回空字符串(静态的标题结构指南照常在写稿提示词里)。"""
    if not cal:
        return ""
    good, bad = [], []
    for r in cal["rows"]:
        if r["verdict"] not in ("sig_pos", "sig_neg") or min(r["n_with"], r["n_without"]) < HINT_MIN_SIDE:
            continue
        if r["group"] == "struct" and r["key"] in STRUCT_HINTS and r["verdict"] == "sig_pos":
            good.append(STRUCT_HINTS[r["key"]])
        elif r["group"] == "feat" and r["key"] == "fluff" and r["verdict"] == "sig_neg":
            bad.append("标题主要靠空泛夸词、缺少具体信息")
        elif r["group"] == "feat" and r["key"] in ("specific", "benefit", "audience", "gap") and r["verdict"] == "sig_pos":
            good.append(FEAT_NAMES[r["key"]][0].replace("标题", "标题里"))
    if not good and not bad:
        return ""
    lines = [f"【标题结构参考】来自你们账号 {cal['n_used']} 篇真实笔记标题的结构分析(表现 = 相对本账号平均,已按发布天数校正),只是倾向,真实、合规和其他要求优先:"]
    if good:
        lines.append("- 表现明显更好的:" + ";".join(dict.fromkeys(good)))
    if bad:
        lines.append("- 表现明显偏差的:" + ";".join(bad))
    lines.append("- 主标题和备选标题都照这个来,但几篇之间结构要有变化。")
    return "\n".join(lines)


_hint_cache: dict = {"at": 0.0, "txt": ""}


def rule_for(draft_id: int, enabled: bool = True, min_days: int = 3, ttl: float = 600.0) -> str:
    """给这篇稿子的标题写法提示(空字符串 = 不加:总开关关了、对照组、没有明显规律)。结果缓存 10 分钟,不用每篇都重算。"""
    import time
    if not enabled or is_control(draft_id):
        return ""
    if time.time() - _hint_cache["at"] > ttl:
        from . import qianfan
        try:
            items, at = qianfan.notes_all()
            _hint_cache.update(at=time.time(), txt=struct_hint(calibrate_struct(items, tag_map(), min_days)))
        except Exception:   # 取不到千帆数据就不加提示,不能影响出稿
            _hint_cache.update(at=time.time(), txt="")
    return ("\n\n" + _hint_cache["txt"]) if _hint_cache["txt"] else ""


_cache: dict = {}


def cached(items: list[dict], data_at: str, min_days: int = 3) -> dict:
    """同一份千帆数据只算一次(复盘每次打开都要用)。"""
    key = (data_at, len(items), min_days, datetime.date.today().isoformat())
    if _cache.get("key") != key:
        _cache.update(key=key, val=calibrate(items, min_days))
    return _cache["val"]
