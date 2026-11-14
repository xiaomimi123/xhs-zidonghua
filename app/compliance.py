"""成稿合规扫描:纯规则匹配,结果稳定可复现。
高风险(极限词、站外导流、收益/疗效承诺、标题超长)会影响过审或限流,必须改;
低风险(AI 腔、营销黑话)只影响观感,建议改。另外列出待作者补充的占位符。"""
import re
from .db import q_all

CATEGORIES = {
    "extreme": ("广告法极限词", "high"),
    "traffic": ("站外导流", "high"),
    "promise": ("收益 / 疗效承诺", "high"),
    "ai": ("AI 腔", "low"),
    "jargon": ("营销黑话", "low"),
    "claim": ("功效宣称", "low"),  # 养生类常见说法,只提示不拦截,不触发自动修正
    "buyer": ("买家口吻(平台判违规)", "high"),   # 商家账号以第一人称买家口吻写消费体验、假装消费者求推荐:小红书按「隐藏卖家身份」处理
}
FIELDS = {"title": "标题", "content": "正文", "tags": "话题"}
TITLE_MAX = 20
PLACEHOLDER = re.compile(r"【补充[:：]?\s*([^】]*)】")


def _positions(hay: str, needle: str) -> list[int]:
    out, i = [], hay.find(needle)
    while i >= 0:
        out.append(i)
        i = hay.find(needle, i + 1)
    return out


def _count(text: str, word: str, exclude: list[str]) -> int:
    """统计 word 出现次数;落在排除短语内的不算(如「第一步」里的「第一」)。"""
    hay, w = text.lower(), word.lower()
    covered = [(s, s + len(e)) for e in exclude for s in _positions(hay, e.lower())]
    return sum(1 for i in _positions(hay, w) if not any(s <= i and i + len(w) <= t for s, t in covered))


IMG_TEXT = re.compile(r"[「“\"]([^」”\"]{1,40})[」”\"]")  # 配图提示词里被引号括起来的,就是要显示在图上的文字


def image_texts(prompts) -> list[str]:
    return [t for p in (prompts or []) for t in IMG_TEXT.findall(str(p))]


def scan(title: str, content: str, tags: list[str], image_prompts=None) -> dict:
    """image_prompts:配图提示词。里面「」括起来的文字会显示在图片上,也要过一遍词库。"""
    fields = {"title": title or "", "content": content or "", "tags": " ".join(tags or []),
              "image_text": " | ".join(image_texts(image_prompts))}
    hits = []
    for r in q_all("SELECT * FROM rules WHERE enabled=1 ORDER BY category, LENGTH(word) DESC"):
        ex = [e.strip() for e in (r["exclude"] or "").split(",") if e.strip()]
        for f, text in fields.items():
            n = _count(text, r["word"], ex)
            if n:
                cat, sev = CATEGORIES.get(r["category"], (r["category"], "low"))
                hits.append({"word": r["word"], "field": f, "count": n, "category": cat, "severity": sev,
                             "replacement": r["replacement"]})
    # 短词被长词包含时只报长词(如「最高级」命中后不再单报「最高」)
    hits = [h for h in hits if not any(o is not h and o["field"] == h["field"] and h["word"] in o["word"]
                                       and o["word"] != h["word"] for o in hits)]
    if len(fields["title"]) > TITLE_MAX:
        hits.insert(0, {"word": f"标题 {len(fields['title'])} 字", "field": "title", "count": 1, "category": "标题超长",
                        "severity": "high", "replacement": f"压到 {TITLE_MAX} 字以内"})
    hits += buyer_hits(fields["title"], fields["content"])
    holes = [{"field": f, "text": m.group(1).strip() or "待补充"} for f, t in fields.items() for m in PLACEHOLDER.finditer(t)]
    # 第一人称的使用感受/吃法/体验:爆款笔记的正常写法,默认视为作者本人的经历,自动确认,不拦截、不扣分、不改写。
    # 只记录下来给页面显示,verify 保持为空(老草稿里存的 verify 页面也不再展示)。
    confirmed = [{"field": f, "text": s} for f in ("title", "content") for s in _claims(fields[f])]
    return {"hits": hits, "placeholders": holes, "verify": [], "confirmed": confirmed,
            "high": sum(1 for h in hits if h["severity"] == "high"),
            "low": sum(1 for h in hits if h["severity"] == "low")}


# 第一人称的经历/测试/时长说法:默认自动确认(见 scan),只做记录
CLAIMS = [
    re.compile(r"我(这次|最近|亲自|自己)?(做了|进行了|拿)?[^。！？\n]{0,10}(实测|测评|测试|对比测|测了)"),
    re.compile(r"(用了|坚持了|连续|试了|回购了)\s*[\d一二三四五六七八九十两半]+\s*(天|周|个月|月|年|次|瓶|支)"),
    re.compile(r"我(拿|买了|入了|囤了)\s*[\d一二三四五六七八九十两]+\s*(款|支|瓶|个|盒)"),
    re.compile(r"我的(反应|感受|体验|评分|经验|吃法|做法|用法|习惯|心得)"),
    re.compile(r"我(自己|一般|平时|通常|现在|每天|每次|最近|这周|昨天|之前)[^。！？\n]{0,14}(吃|喝|用|做|配|试|买|煮|泡|冲|加)"),
    re.compile(r"(买|入|囤)了.{0,8}(回来|回家).{0,4}(试|喝|尝|用)"),
    re.compile(r"盲测|实测|亲测|试喝|试吃"),
]


def _claims(text: str) -> list[str]:
    out = []
    for sent in re.split(r"(?<=[。！？!?\n])", text):
        if "【补充" in sent:  # 已经留给作者补的句子不再提示
            continue
        for p in CLAIMS:
            m = p.search(sent)
            if m:
                out.append(sent.strip()[:60])
                break
    return out


# 商家账号不能装成买家(小红书规则:故意隐藏卖家身份、以第一人称买家口吻描述消费体验)。
# 买家的购买 / 试用 / 时长 / 求推荐这类说法不管有没有亮身份都算;「我平时这样吃」这类习惯说法,亮明了商家身份(我们家 / 店主 / 做了几年…)的放过。
BUYER_ALWAYS = [
    re.compile(r"我(刚|已经|终于|最近|这次|昨天|之前|前几天|上周)?(买了|入手了|入了|囤了|回购了?|下单了?|收到了?|拿到了?|试了|试吃了?|试喝了?|喝了|吃了|用了)"),
    re.compile(r"亲测|实测|盲测|自用|回购|种草了|拔草"),
    re.compile(r"(用了|坚持了|连续|喝了|吃了)\s*[\d一二三四五六七八九十两半]+\s*(天|周|个月|月|年)"),
    re.compile(r"(闺蜜|同事|朋友|室友|家人)(推荐|安利|让我)"),
    re.compile(r"(求|有没有|谁知道)[^。！？\n]{0,8}(推荐|链接|同款|哪里买|怎么选)"),
    re.compile(r"我的(反应|感受|体验|评分|心得|使用感受)"),
]
BUYER_HABIT = [re.compile(r"我(自己|一般|平时|通常|每天|每次|现在)[^。！？\n]{0,14}(吃|喝|用|配|泡|冲|做)")]
SELLER_ID = re.compile(r"我们家|我们店|我们工厂|我们品牌|我们的(店|工厂|品牌|团队)|店主|老板|商家|厂家|品牌方|做了[^。！？\n]{0,3}年|我是做|我家(店|工厂)")


def buyer_voice_hits(title: str, content: str) -> list[dict]:
    """买家口吻命中:[{word, field, count}]。含【补充:…】占位符的句子跳过(留给作者填)。亮明商家身份时只看 BUYER_ALWAYS。"""
    out = []
    seller = bool(SELLER_ID.search(f"{title}\n{content}"))
    pats = BUYER_ALWAYS if seller else BUYER_ALWAYS + BUYER_HABIT
    for field, text in (("title", title or ""), ("content", content or "")):
        found: dict[str, int] = {}
        for sent in re.split(r"(?<=[。！？!?\n])", text):
            if "【补充" in sent:
                continue
            for p in pats:
                for m in p.finditer(sent):
                    w = m.group(0)[:14]
                    found[w] = found.get(w, 0) + 1
        out += [{"word": w, "field": field, "count": n} for w, n in list(found.items())[:5]]
    return out


def buyer_hits(title: str, content: str) -> list[dict]:
    """买家口吻命中,整理成 scan 里 hits 的格式(高风险)。"""
    return [{**b, "category": CATEGORIES["buyer"][0], "severity": "high",
             "replacement": "改成商家口吻(「我们家…」)或无主语的做法 / 建议,去掉「我买了 / 亲测 / 用了 N 天」这类买家说法"} for b in buyer_voice_hits(title, content)]


def with_buyer(checks, title: str, content: str) -> dict:
    """给已经存好的质检结果补上(或刷新)买家口吻这一项,并重算高 / 低风险个数。老稿子在加这条规则之前扫描过,用它补扫;重复调用结果不变。"""
    checks = dict(checks) if isinstance(checks, dict) else {}
    cat = CATEGORIES["buyer"][0]
    hits = [h for h in checks.get("hits") or [] if h.get("category") != cat] + buyer_hits(title, content)
    checks["hits"] = hits
    checks["high"] = sum(1 for h in hits if h.get("severity") == "high")
    checks["low"] = sum(1 for h in hits if h.get("severity") == "low")
    return checks


def banned_for_prompt() -> str:
    """写作时提前告诉模型的禁用词(只给高风险类,避免提示词过长)。"""
    words = {}
    for r in q_all("SELECT category, word FROM rules WHERE enabled=1 AND category IN ('extreme','traffic','promise')"):
        words.setdefault(CATEGORIES[r["category"]][0], []).append(r["word"])
    return "\n".join(f"- {k}:{'、'.join(v)}" for k, v in words.items())
