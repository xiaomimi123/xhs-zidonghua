"""封面和配图规划:每篇笔记随机 1~4 张图,第 1 张是程序排版的封面(版式 + 配色按内容挑选、同一批里轮换),
其余是 AI 生成的无字实拍图(场景库让每张换不同场景)。字全部由程序排,AI 不画字。"""
import math
import random
import re

from . import cards
from .db import get_settings

TEXT_LAYOUTS = ["statement", "memo", "checklist", "big_number", "question", "swiss", "quote", "stripe"]
PHOTO_LAYOUTS = ["photo_top", "split_top", "split_bottom", "editorial", "sticker", "polaroid"]
PHOTO_ONLY = "photo_only"   # 纯产品图封面:一张不带字的产品实拍图,只在任务有产品时参与轮换
THEME_KEYS = list(cards.THEMES)


def kind_of(layout: str) -> str:
    """封面类型(记在稿子上,复盘里按它统计哪类更容易爆):html = 程序排版纯文字,photo_text = 实拍图 + 程序排的字,photo = 纯产品图。"""
    return "photo" if layout == PHOTO_ONLY else "photo_text" if layout in PHOTO_LAYOUTS else "html"
# 实拍封面背景的构图要求:给程序排版的文字留出位置
COMPOSE = {
    "photo_only": "产品是画面主角,占画面一半以上,居中偏下,背景是真实的生活场景,构图饱满、有层次,不留文字区域",
    "photo_top": "主体放在画面下半部分,上方三分之一是干净的留白区域",
    "photo_top_1": "主体放在画面上半部分,下方三分之一是干净的留白区域",
    "sticker_1": "主体居中偏下,画面上方留出干净的区域",
    "split_top": "主体居中偏下,上方留出干净的背景",
    "split_bottom": "主体居中,构图饱满",
    "editorial": "主体居中,横向构图感,四周留少量空白",
    "sticker": "主体居中偏上,画面下方留出干净的区域",
    "polaroid": "主体居中,背景简洁",
}
# 生图统一风格:要的是普通人用手机随手拍的生活照,不是棚拍广告大片(商业感太重会一眼被认出是 AI / 广告)
UGC_STYLE = ("手机随手拍的生活照风格:iPhone 直出没有修图,家里的自然光(可以略微过曝或偏暗),构图随意不追求完美,"
             "背景里有日常的生活杂物,带一点手机的噪点和虚焦;不是棚拍,不是广告大片,没有专业布光,不要精致摆盘")
# 场景库:每个场景一个名字,按产品类型挑能放的场景(咖啡不放在炒菜的灶台边,零食不放在厨房烹饪场景)
SCENES = {
    "table": "家里的餐桌一角,窗边自然光,旁边有碗筷和没收拾的杯子",
    "breakfast": "早餐桌上有面包、水果和牛奶,手边放着主体,侧逆光,桌面有水渍和面包屑",
    "rice": "刚做好的饭菜俯拍,碗盘随意摆放,桌面有水渍和纸巾",
    "office": "办公室工位桌面,午休时间,键盘和水杯一角入镜",
    "desk_night": "夜晚台灯下的书桌一角,暖黄灯光,桌上有书和零食包装",
    "sofa": "沙发前的茶几,边吃边拍,旁边有手机和遥控器",
    "cafe": "咖啡馆靠窗的小桌,木质桌面,绿植虚化",
    "coffee_corner": "家里的咖啡角:台面上有杯子、小勺子和奶壶,整洁的居家角落,自然光",
    "window_tea": "窗边的小桌上下午茶,杯子冒着热气,旁边有书和手机,午后光线",
    "kitchen": "厨房台面,旁边有调料瓶和砧板,食材随手放着,明亮的自然光",
    "stove": "灶台边正在做饭,台面有些油渍和调料",
    "hand": "一只手拿着主体举在镜头前,背景是虚化的室内",
    "bag": "买回家放在桌上的状态,购物袋还没收,日常杂乱",
    "picnic": "户外野餐垫上,自然光,有水果和面包点缀",
}
FOOD_SCENES = list(SCENES.values())   # 全部美食场景(Live 页的场景下拉等要列出所有场景的地方用)
# 按产品类型能出现的场景(灶台、锅具只给真正需要烹饪的产品)
KIND_POOLS = {
    "drink": ["window_tea", "coffee_corner", "cafe", "office", "breakfast", "sofa", "hand", "desk_night"],
    "snack": ["office", "sofa", "desk_night", "window_tea", "picnic", "hand", "bag"],
    "cook": ["kitchen", "stove", "table", "rice", "hand", "bag", "breakfast"],
    "mixed": ["window_tea", "coffee_corner", "kitchen", "breakfast", "office", "table", "hand"],   # 既能冲饮又能入菜(肉桂粉、姜黄粉这类):不放灶台
    "food": ["table", "breakfast", "rice", "hand", "office", "sofa", "desk_night", "bag"],
}
# 产品资料里「可以带出产品的场景」的关键词 → 优先用哪些场景
TAG_KEYS = [("办公|工位|提神|上班", "office"), ("早餐|佐餐", "breakfast"), ("下午茶|咖啡角|冲泡|饮用|热饮|拿铁|睡前", "window_tea"), ("咖啡角|自制咖啡", "coffee_corner"),
            ("追剧|休闲|零食|分享", "sofa"), ("夜|睡前|助眠", "desk_night"), ("烹饪|烘焙|调味|炒|煮", "kitchen"), ("拌饭|下饭|配粥|米饭", "rice"), ("野餐|户外", "picnic")]
_DRINK, _SNACK, _COOK = (re.compile(x) for x in (r"咖啡|茶|冲饮|奶|饮|麦片|燕麦|可可粉|豆浆|拿铁|冲泡", r"饼|零食|坚果|糖|巧克力|脆|薯|果干|蛋糕|点心", r"烹饪|烘焙|调味|香料|食用油|盐|醋|酱油|炒菜|料理"))


def product_kind(prod: dict | None) -> str:
    """产品属于哪一类(决定能放进哪些场景):drink 冲泡饮品 / snack 零食点心 / cook 烹饪调味 / mixed 两用 / food 一般食品。"""
    if not prod:
        return "food"
    prof = prod.get("profile") or {}
    text = f"{prod.get('name') or ''} {prod.get('category') or prof.get('category') or ''}"
    scenes = " ".join(prof.get("scenes") or [])
    if _SNACK.search(text) and not _DRINK.search(text.replace("可可", "")):
        return "snack"
    d, c = bool(_DRINK.search(text) or _DRINK.search(scenes)), bool(_COOK.search(text) or _COOK.search(scenes))
    return "mixed" if d and c else "drink" if d else "cook" if c else "snack" if _SNACK.search(scenes) else "food"
GENERIC_SCENES = [
    "家里书桌一角,自然光,桌上有水杯和笔记本,随手放着", "客厅茶几俯拍,遥控器和杂志随意摆放",
    "手持特写,背景是家里虚焦的客厅", "卧室床头柜一角,台灯暖光,旁边有手机充电线",
    "阳台窗台,午后光线,旁边有晾着的衣物和绿植", "办公桌面一角,工位上有键盘和咖啡杯",
    "餐边柜台面,日常杂物随意堆放", "浴室台面,镜子边,日常用品随手放着",
    "沙发扶手旁,一只手拿着主体,背景是家里", "餐桌一角,自然光,桌面有些许杂物",
]

NO_TEXT = "画面中不要出现任何文字、字母、标签或水印"


# ---------- 张数 ----------
def image_count(s: dict | None = None, rng: random.Random | None = None) -> int:
    """每篇笔记的图片总张数(含封面):按管理端设置的比例随机在 1~4 之间选。"""
    s = s or get_settings()
    try:
        w = [max(0.0, float(x)) for x in str(s.get("image_count_weights") or "").replace("，", ",").split(",")][:4]
    except ValueError:
        w = []
    if len(w) != 4 or sum(w) <= 0:
        w = [20, 30, 30, 20]
    return (rng or random).choices([1, 2, 3, 4], weights=w)[0]


def scene(track: str, did: int, i: int, prod: dict | None = None) -> str:
    """配图的场景方向:美食按产品类型挑能放的场景(并优先用产品资料里「可以带出产品的场景」),其它赛道用通用场景;同一篇里每张换一个。"""
    if track != "美食":
        return GENERIC_SCENES[(did * 3 + i * 7) % len(GENERIC_SCENES)]
    kind = product_kind(prod)
    pool = KIND_POOLS[kind]
    labels = " ".join(((prod or {}).get("profile") or {}).get("scenes") or [])
    pref = [k for pat, k in TAG_KEYS if re.search(pat, labels) and k in pool]
    cand = list(dict.fromkeys(pref + pool))
    return SCENES[cand[(did * 3 + i * 7) % len(cand)]]


# ---------- 封面文字 ----------
def _cut(t: str, n: int) -> str:
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    if len(t) <= n:
        return t
    seg = next((s for s in re.split(r"[｜|!！?？,，。:：;；]", t) if 3 <= len(s.strip()) <= n), "")
    return seg.strip() or t[:n]


def numbered_points(content: str, n: int = 4) -> list[str]:
    """从正文里取编号/分点的行当要点:「① 配粥」「1. 拌面」「一、炒菜」。"""
    out = []
    for line in (content or "").splitlines():
        m = re.match(r"^\s*(?:[①②③④⑤⑥⑦⑧⑨⑩]|\d{1,2}\s*[.、)）]|[一二三四五六七八九十]\s*[、.])\s*(.+)$", line)
        if m:
            t = _cut(re.split(r"[:：,，。]", m.group(1))[0], 14)
            if t:
                out.append(t)
    return out[:n]


def clean_cover(raw: dict, out: dict, safe=lambda t: t) -> dict:
    """规范化模型给的封面信息:限制长度,缺的用标题和正文补;safe 用来过滤高风险词。"""
    raw = raw if isinstance(raw, dict) else {}
    title = safe(_cut(raw.get("title") or out.get("title") or "", 16)) or safe(_cut(out.get("title") or "", 16))
    pts = [safe(_cut(p, 14)) for p in (raw.get("points") or []) if str(p).strip()]
    pts = [p for p in pts if p]
    if len(pts) < 3:
        pts = [safe(p) for p in numbered_points(out.get("content") or "")] or pts
    num = (re.search(r"\d{1,2}", str(raw.get("num") or "")) or re.search(r"(?<!\d)(\d{1,2})(?=\s*(?:种|个|步|招|款|天|分钟|道|类))", out.get("title") or "") or [None])[0] or ""
    return {"title": title, "sub": safe(_cut(raw.get("sub"), 18)), "points": pts[:4], "num": num,
            "unit": _cut(raw.get("unit"), 6) if num else "", "tag": safe(_cut(raw.get("tag"), 6)),
            "scene": re.sub(r"[「『“\"][^」』”\"]*[」』”\"]", "", str(raw.get("scene") or "")).strip()[:120]}


# ---------- 版式和配色 ----------
def _batch_order(pid: int, salt: str, items: list) -> list:
    """同一个任务(批次)里固定的一个打乱顺序:不同任务顺序不同,同一任务里按它轮换。"""
    r = random.Random(f"{pid}:{salt}")
    xs = list(items)
    r.shuffle(xs)
    return xs


def choose(did: int, cover: dict, title: str = "", pid: int = 0, idx: int | None = None, has_product: bool = False) -> tuple[str, str]:
    """挑封面版式和配色。pid = 任务 id,idx = 这篇是任务里的第几篇(不传就用稿子 id)。
    一批里三类封面轮着来:实拍图 + 排字(要生一张实拍背景)、程序排版的纯文字(不花生图钱)、纯产品图(任务有产品时才有),
    每类各自按这个任务固定的顺序轮换,所以 10 篇里版式最多重复一两次,相邻的类型一定不同;配色在 11 套里按步长轮换。
    封面信息不够的版式(没有要点不能用清单类、没有数字不能用大数字)会跳过。"""
    idx = did if idx is None else idx
    ok = set(compatible(cover, True))
    photo = [x for x in _batch_order(pid, "p", PHOTO_LAYOUTS) if x in ok]
    text = [x for x in _batch_order(pid, "t", TEXT_LAYOUTS) if x in ok]
    kinds = [photo, text] + ([[PHOTO_ONLY]] if has_product else [])
    pool = kinds[(pid + idx) % len(kinds)] or photo or text
    layout = pool[(idx // len(kinds)) % len(pool)]
    themes = _batch_order(pid, "th", THEME_KEYS)
    step = next(k for k in (3, 5, 7, 1) if math.gcd(k, len(themes)) == 1)
    return layout, themes[(idx * step) % len(themes)]


def compatible(cover: dict, has_photo: bool, product: bool = False) -> list[str]:
    """这篇的封面信息能用哪些版式(要点不够不能用清单类,没有数字不能用大数字,没有现成实拍图时只用纯文字版式,免得多花一张图)。"""
    n = len(cover.get("points") or [])
    ok = list(PHOTO_LAYOUTS) + ([PHOTO_ONLY] if product else []) if has_photo else []
    ok += ["statement", "quote", "stripe"]
    if re.search(r"[?？]|吗|怎么|到底|为什么|如何", str(cover.get("title") or "")):
        ok.append("question")
    if n >= 3:
        ok += ["checklist", "memo"]
    if n >= 2:
        ok.append("swiss")
    if cover.get("num"):
        ok.append("big_number")
    return ok


def next_style(cover: dict, has_photo: bool, rng: random.Random | None = None) -> tuple[str, str]:
    """「换个封面样式」:换一个和当前不同的版式 + 配色。"""
    r = rng or random
    prod = bool(cover.get("product"))
    opts = [x for x in compatible(cover, has_photo, prod) if x != cover.get("layout")] or compatible(cover, has_photo, prod)
    theme = r.choice([t for t in THEME_KEYS if t != cover.get("theme")] or THEME_KEYS)
    return r.choice(opts), theme


def cover_line(cc: dict) -> str:
    """写进 image_prompts[0] 的封面说明:给人看,也让合规扫描能扫到封面上的字。"""
    label, need, _ = cards.LAYOUTS[cc["layout"]]
    if cc["layout"] == PHOTO_ONLY:   # 纯产品图封面不排字,说明里不放标题(也不用过图上文字的合规扫描)
        return f"封面·{label}:实拍 {cc.get('scene') or '产品特写'},不排字"
    texts = [cc.get("title"), cc.get("sub"), *(cc.get("points") or []), cc.get("tag"), f"{cc.get('num') or ''}{cc.get('unit') or ''}"]
    return (f"封面·{label}:" + (f"实拍背景 {cc.get('scene') or '产品特写'} " if need else "纯文字封面 ")
            + "文字:" + "".join(f"「{t}」" for t in texts if t))


def photo_prompt(cc: dict, with_product: bool) -> str:
    head = "以参考图中的产品为主体,严格保持产品的外观、颜色、包装和包装上的文字不变。" if with_product else ""
    return (f"{head}{cc.get('scene') or '产品特写,放在家里的桌面上,背景是日常的生活杂物'}。{COMPOSE.get(cc.get('layout') + '_1' if cc.get('variant') == 1 and (cc.get('layout') + '_1') in COMPOSE else cc.get('layout'), '主体居中')}。"
            f"{UGC_STYLE},3:4 竖构图。{NO_TEXT}。")


def inner_prompts(raw, k: int, track: str, did: int, title: str, prod: dict | None = None) -> list[str]:
    """内页配图提示词:去掉所有会显示成文字的「」内容,补齐/裁剪到 k 条,每条都要求画面无字。"""
    out = []
    for p in raw or []:
        t = re.sub(r"[「『“\"][^」』”\"]*[」』”\"]", "", str(p))
        t = re.sub(r"[,，;；]?\s*(画面中不要出现[^。,，;；]*|图中不要出现[^。,，;；]*)", "", t).strip(" ,，。;；")
        if t:
            out.append(f"{t},{NO_TEXT}")
    while len(out) < k:
        out.append(f"{title}的主体特写,{scene(track, did, len(out) + 1, prod)},{NO_TEXT}")
    return out[:k]


def plan(did: int, out: dict, track: str, brand: str, n: int, safe=lambda t: t, pid: int = 0, idx: int | None = None, has_product: bool = False, prod: dict | None = None) -> tuple[list[str], dict]:
    """返回 (image_prompts, cover_card)。image_prompts[0] 是封面说明,其余是内页提示词,共 n 条。"""
    cc = clean_cover(out.get("cover"), out, safe)
    cc["brand"] = brand
    cc["product"] = bool(has_product)
    cc["layout"], cc["theme"] = choose(did, cc, out.get("title") or "", pid, idx, has_product)
    cc["kind"] = kind_of(cc["layout"])
    cc["variant"] = (((did if idx is None else idx) // 2) + pid) % 2   # 版式的小变体(标题在上 / 在下等),同版式的封面也不一个样
    return [cover_line(cc), *inner_prompts(out.get("image_prompts"), n - 1, track, did, out.get("title") or "", prod)], cc
