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
             "背景整洁自然,只有一两件日常小物,带一点手机的噪点和虚焦;不是棚拍,不是广告大片,没有专业布光,不要精致摆盘。"
             "画面里不要出现烟雾、蒸汽或热气(热饮的杯口除外),不要出现成团的纸巾、污渍和杂乱堆放的杂物")
# 偶尔(约三分之一的图)带一点「随手拍」的痕迹,让画面有生活感,但不乱:不是纸巾团、水渍、杂物堆
LIGHT_TRACES = ["桌角有一个用过的杯子", "旁边摊开着一本书", "桌上放着手机", "旁边有一个拆开的快递盒", "杯垫上留着一圈浅浅的水印",
                "椅背上搭着一件外套", "旁边有一双筷子随意搁着"]


def ugc_style(rng=None) -> str:
    """生图统一风格;约三分之一的图额外带一点随手拍的痕迹(整洁为主,偶尔有生活感)。"""
    rng = rng or random
    return UGC_STYLE + (f"。可以带一点随手拍的痕迹:{rng.choice(LIGHT_TRACES)}" if rng.random() < 0.34 else "")


# 场景库:每个场景一个名字,按产品类型挑能放的场景(咖啡不放在炒菜的灶台边,零食不放在厨房烹饪场景)
SCENES = {
    "table": "家里的餐桌一角,窗边自然光,桌面整洁,旁边有碗筷",
    "breakfast": "早餐桌上有面包、水果和牛奶,手边放着主体,侧逆光,桌面有一点面包屑",
    "rice": "家常饭菜的餐桌俯拍,一碗白米饭和两样家常小菜,碗盘摆放自然",
    "office": "办公室工位桌面,午休时间,键盘和水杯一角入镜",
    "desk_night": "夜晚台灯下的书桌一角,暖黄灯光,桌上有书和笔记本",
    "sofa": "沙发前的茶几,边吃边拍,旁边有手机和遥控器",
    "cafe": "咖啡馆靠窗的小桌,木质桌面,绿植虚化",
    "coffee_corner": "家里的咖啡角:台面上有杯子、小勺子和奶壶,整洁的居家角落,自然光",
    "window_tea": "窗边的小桌上下午茶,桌上有杯子、书和手机,午后光线",
    "kitchen": "厨房台面,明亮的自然光,台面整洁,旁边有几个调味瓶",
    "stove": "灶台边的台面,家常做饭的场景,锅具和调料放得整齐,不要出现烟雾",
    "hand": "一只手拿着主体举在镜头前,背景是虚化的室内",
    "bag": "刚买回家放在桌上的状态,旁边是拆开的快递盒",
    "picnic": "户外野餐垫上,自然光,有水果和面包点缀",
}
SCENES.update({
    "congee": "早餐桌上一碗白粥配两样小菜,侧面自然光,桌面整洁",
    "noodle": "餐桌上一碗拌好的面条,旁边放着筷子和小碟,俯拍",
    "lunchbox": "午休时的办公桌,打开的便当盒和一杯水,旁边放着手机",
    "dinner": "晚饭前的餐桌,几样家常菜摆好,暖色顶灯",
    "dipping": "围坐吃饭的餐桌一角,几个小蘸料碟和筷子",
    "prep": "备菜的厨房台面,切好的蔬菜装在小碗里,砧板干净",
    "pantry": "橱柜里整齐摆放的调味品和罐头,柜门打开",
    "bedside": "床头柜上亮着暖光台灯,旁边是一本书,睡前的氛围",
    "deskcup": "办公桌上靠近电脑的位置,旁边有一个杯子和一本笔记本",
    "blender": "厨房台面上的破壁机旁,放着水果和燕麦",
    "yoga": "运动后的客厅地垫旁,一瓶水和毛巾叠放整齐",
    "dorm": "宿舍书桌一角,台灯、书和水杯整齐摆放",
    "school": "学生书桌,摊开的作业本和笔袋,台灯亮着",
    "movie": "雨夜的客厅,沙发上的小毯子和茶几上的零食",
    "car": "车里副驾驶前的置物台,旁边有一瓶水和一个小包",
    "gym": "健身房更衣柜前的长凳,一个运动包和水杯",
    "park": "公园长椅上,身旁是树荫和一个布袋,午后光线",
    "camping": "露营折叠桌,旁边是帐篷一角和保温杯",
    "balcony": "阳台的小圆桌,旁边有绿植和一杯茶,午后光线",
    "windowsill": "窗台上,午后阳光,旁边有一盆绿植",
})
# 换背景页的备选背景只用最初的 14 种(那里是一排按钮,几十个会挤满页面);其余场景按产品类型在生成场景图时使用
BG_SCENE_KEYS = ["table", "breakfast", "rice", "office", "desk_night", "sofa", "cafe", "coffee_corner", "window_tea", "kitchen", "stove", "hand", "bag", "picnic"]
BG_SCENES = [SCENES[k] for k in BG_SCENE_KEYS]
FOOD_SCENES = list(SCENES.values())   # 全部美食场景
# 按产品类型能出现的场景(灶台、锅具只给真正需要烹饪的产品)
KIND_POOLS = {
    "drink": ["window_tea", "coffee_corner", "cafe", "office", "breakfast", "sofa", "hand", "desk_night", "bedside", "deskcup", "blender", "balcony", "yoga", "dorm"],
    "snack": ["office", "sofa", "desk_night", "window_tea", "picnic", "hand", "bag", "lunchbox", "dorm", "school", "movie", "car", "gym", "park", "camping"],
    "cook": ["kitchen", "stove", "table", "rice", "hand", "bag", "breakfast", "prep", "pantry", "dinner", "noodle", "dipping"],
    "mixed": ["window_tea", "coffee_corner", "kitchen", "breakfast", "office", "table", "hand", "blender", "bedside", "pantry", "balcony"],   # 既能冲饮又能入菜(肉桂粉、姜黄粉这类):不放灶台
    "food": ["table", "breakfast", "rice", "hand", "office", "sofa", "desk_night", "bag", "lunchbox", "dinner", "congee", "noodle"],
    "side": ["table", "rice", "breakfast", "hand", "bag", "office", "congee", "noodle", "lunchbox", "dinner", "dipping"],   # 下饭佐餐的小菜(腐乳、酱菜、榨菜):配饭、配粥、拌面,不放咖啡角
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
    k = (prof.get("shoot") or {}).get("kind")   # AI 按这个产品生成的拍摄规格里的类型,比下面的关键词猜测准
    if k in KIND_POOLS:
        return k
    text = f"{prod.get('name') or ''} {prod.get('category') or prof.get('category') or ''}"
    scenes = re.sub(r"下午茶|茶歇", "", " ".join(prof.get("scenes") or []))   # 「办公室下午茶零食」不是冲饮,别因为有个「茶」字就当饮品
    if _SNACK.search(text) and not _DRINK.search(text.replace("可可", "")):
        return "snack"
    d, c = bool(_DRINK.search(text) or _DRINK.search(scenes)), bool(_COOK.search(text) or _COOK.search(scenes))
    return "mixed" if d and c else "drink" if d else "cook" if c else "snack" if _SNACK.search(scenes) else "food"
GENERIC_SCENES = [
    "家里书桌一角,自然光,桌上有水杯和笔记本", "客厅茶几俯拍,遥控器和杂志摆在一旁",
    "手持特写,背景是家里虚焦的客厅", "卧室床头柜一角,台灯暖光,旁边有手机充电线",
    "阳台窗台,午后光线,旁边有绿植", "办公桌面一角,工位上有键盘和咖啡杯",
    "餐边柜台面,摆着几件日常用品", "浴室台面,镜子边,日常用品摆放整齐",
    "沙发扶手旁,一只手拿着主体,背景是家里", "餐桌一角,自然光,桌面整洁",
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


def scene_pool(track: str, prod: dict | None = None) -> list[str]:
    """这个赛道 / 产品能用的场景(美食按产品类型挑,并优先用产品资料里「可以带出产品的场景」;其它赛道用通用场景)。"""
    if track != "美食":
        return list(GENERIC_SCENES)
    pool = KIND_POOLS[product_kind(prod)]
    labels = " ".join(((prod or {}).get("profile") or {}).get("scenes") or [])
    pref = [k for pat, k in TAG_KEYS if re.search(pat, labels) and k in pool]
    own = [x for x in (((prod or {}).get("profile") or {}).get("shoot") or {}).get("scenes") or [] if isinstance(x, str) and x.strip()]   # AI 按这个产品推荐的专属场景
    return list(dict.fromkeys(own + [SCENES[k] for k in dict.fromkeys(pref + pool)]))


def scene(track: str, did: int, i: int, prod: dict | None = None) -> str:
    """配图的场景方向:同一篇里每张换一个。"""
    cand = scene_pool(track, prod)
    n = len(cand)
    step = next(k for k in (7, 5, 11, 3, 13, 1) if math.gcd(k, n) == 1)   # 步长要和场景数互质,同一篇里连着的几张才不会重复(场景数是 7 或 14 时,原来固定的 7 会让每张都是同一个)
    return cand[(did * 3 + i * step) % n]


# 拍法:同一个场景也能拍出不一样的画面(机位 × 光线 × 画面状态)。只换场景的话,同一产品反复生成,画面会越来越像。
# 参考图里产品常常是「盒子 + 罐子并排」,模型会照搬这个摆法,所以「画面状态」里要有会真正改变构图的选项(只拍一件、手拿着、错开摆放…)
LOOK_ANGLES = ["平视角度,手机离主体约半米", "从上往下俯拍", "45 度侧前方拍,手机略微倾斜", "近距离特写,主体占画面大半,背景虚焦",
               "稍远一点,带出更多周围环境", "低机位,贴近桌面略微仰拍"]
LOOK_LIGHTS = ["白天窗边的自然光", "偏暗的室内灯光,略微暖黄", "午后斜射的阳光,有明显的光影", "傍晚偏暖的光线", "阴天柔和偏冷的光线"]
LOOK_STATES = ["只拍其中一件产品(有多件包装时只拍最主要的那一件),占画面三分之一左右", "一只手拿着产品,其余包装不入镜",
               "产品放在画面前景,周围的物品前后错开摆放,有一点遮挡、不要整齐排成一排(产品只有一个,不要重复出现)", "产品偏向画面一侧,另一侧是几件日常小物", "产品摆得略有倾斜,像随手一放,旁边有用过的痕迹(喝过的杯子、拆开的包装)",
               "产品放在桌角,周围的生活物品占画面大半"]
LOOKS = [f"{a};{l};{s}" for a in LOOK_ANGLES for l in LOOK_LIGHTS for s in LOOK_STATES]   # 180 种


PAIR_WEIGHT = {"high": 3.0, "mid": 1.5, "low": 0.4}


def pick_pairings(count: int, pairings: list[dict], used: dict | None = None, rng=None) -> list[str]:
    """给每张图挑一个「搭配」(产品拍摄规格里的真实吃法 / 搭配)。按频率权重分配名额:常用的多、少用的很少(如腐乳配馒头约每 12 张一次),
    而且是按名额轮流出现,不是碰运气;这个产品以前生成过的也算进去,所以反复生成长期看比例也对得上。没有搭配清单返回空串。"""
    rng = rng or random
    pairings = [x for x in (pairings or []) if x.get("name")]
    if not pairings:
        return [""] * count
    w = {x["name"]: PAIR_WEIGHT.get(x.get("freq"), 1.5) for x in pairings}
    total = sum(w.values())
    use = {k: (used or {}).get(k, 0) for k in w}
    n, out = sum(use.values()), []
    for _ in range(count):
        # 缺口 = 按权重应该出现的次数 - 已经出现的次数;缺口最大的先上,并列的随机
        name = max(w, key=lambda k: w[k] / total * (n + 1) - use[k] + rng.random() * 1e-6)
        out.append(name)
        use[name] += 1
        n += 1
    return out


def pick_looks(count: int, scenes: list[str], used: dict | None = None, rng=None) -> list[tuple[str, str]]:
    """给一批图各挑一个(场景, 拍法):先挑用得最少的场景(一批里先把不同场景用一遍),再挑拍法。
    拍法分机位 / 光线 / 画面状态三项,每项都尽量不重复(用过的次数少的优先),所以连着挑几张,三项都会换,不会只换场景。
    used = {(场景, 拍法): 用过几次}:这个产品以前生成过的,会被避开,所以反复点「生成」也不会越来越像。"""
    rng = rng or random
    looks = LOOKS[:]
    rng.shuffle(looks)
    scenes = list(scenes) or [""]
    taken = dict(used or {})
    scene_use: dict[str, int] = {}
    comp: dict[tuple[int, str], int] = {}   # (第几项, 内容) -> 用过几次
    for (sc, lk), n in taken.items():
        scene_use[sc] = scene_use.get(sc, 0) + n
        for k, part in enumerate(lk.split(";")):
            comp[(k, part)] = comp.get((k, part), 0) + n
    out = []
    for _ in range(count):
        order = scenes[:]
        rng.shuffle(order)
        sc = min(order, key=lambda x: scene_use.get(x, 0))
        lk = min(looks, key=lambda x: (taken.get((sc, x), 0), sum(comp.get((k, p), 0) for k, p in enumerate(x.split(";")))))
        out.append((sc, lk))
        scene_use[sc] = scene_use.get(sc, 0) + 1
        taken[(sc, lk)] = taken.get((sc, lk), 0) + 1
        for k, part in enumerate(lk.split(";")):
            comp[(k, part)] = comp.get((k, part), 0) + 1
    return out


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
    return (f"{head}{cc.get('scene') or '产品特写,放在家里整洁的桌面上,背景是日常的家居环境'}。{COMPOSE.get(cc.get('layout') + '_1' if cc.get('variant') == 1 and (cc.get('layout') + '_1') in COMPOSE else cc.get('layout'), '主体居中')}。"
            f"{ugc_style()},3:4 竖构图。{NO_TEXT}。")


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
