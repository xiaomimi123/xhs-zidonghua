"""封面和配图规划:每篇笔记随机 1~4 张图,第 1 张是程序排版的封面(版式 + 配色按内容挑选、同一批里轮换),
其余是 AI 生成的无字实拍图(场景库让每张换不同场景)。字全部由程序排,AI 不画字。"""
import random
import re

from . import cards
from .db import get_settings

TEXT_LAYOUTS = ["statement", "memo", "checklist", "big_number", "question", "swiss"]
PHOTO_LAYOUTS = ["photo_top", "split_top", "split_bottom", "editorial", "sticker", "polaroid"]
THEME_KEYS = list(cards.THEMES)
# 实拍封面背景的构图要求:给程序排版的文字留出位置
COMPOSE = {
    "photo_top": "主体放在画面下半部分,上方三分之一是干净的留白区域",
    "split_top": "主体居中偏下,上方留出干净的背景",
    "split_bottom": "主体居中,构图饱满",
    "editorial": "主体居中,横向构图感,四周留少量空白",
    "sticker": "主体居中偏上,画面下方留出干净的区域",
    "polaroid": "主体居中,背景简洁",
}
FOOD_SCENES = [
    "清晨窗边的木质餐桌,侧逆光,桌上有热气腾腾的早餐", "厨房大理石操作台俯拍,旁边有食材和厨具",
    "极简棚拍静物,纯色浅色背景,柔和顶光", "一只手拿着主体举在镜头前,背景是虚化的超市货架",
    "户外野餐垫上,自然光,有水果和面包点缀", "办公室工位桌面,午休时间,电脑和笔记本一角入镜",
    "深夜台灯下的书桌,暖黄色灯光,安静氛围", "家庭聚餐的圆桌一角,几道家常菜,暖光",
    "厨房灶台边,锅具和食材,蒸汽升起", "咖啡馆靠窗的小桌,木质桌面,绿植虚化",
]
GENERIC_SCENES = [
    "明亮的白色桌面静物,柔和自然光", "浅木色书桌,侧光,绿植点缀", "手持特写,背景是虚化的室内",
    "极简纯色背景棚拍,柔和顶光", "卧室床头柜一角,台灯暖光", "客厅茶几俯拍,杂志和杯子点缀",
    "户外草地长椅,自然光", "木质托盘与亚麻餐布摆拍", "办公桌面一角,笔记本和咖啡", "窗边飘窗台,午后光线",
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


def scene(track: str, did: int, i: int) -> str:
    lib = FOOD_SCENES if track == "美食" else GENERIC_SCENES
    return lib[(did * 3 + i * 7) % len(lib)]


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
def choose(did: int, cover: dict, title: str = "") -> tuple[str, str]:
    """按内容挑封面版式:有要点 → 清单/备忘录/网格;有数字 → 大数字;问句 → 问句封面;其余实拍图版式和大字海报。
    同一批里用 did 轮换,避免相邻的稿子撞版式。"""
    q = re.search(r"[?？]|吗|怎么|到底|为什么|如何", f"{cover.get('title', '')}{title}")
    pref = []
    if len(cover.get("points") or []) >= 3:
        pref += ["checklist", "memo", "swiss"]
    if cover.get("num"):
        pref.append("big_number")
    if q:
        pref.append("question")
    pref += PHOTO_LAYOUTS + ["statement"]
    pool = list(dict.fromkeys(pref))[:5]
    layout = pool[did % len(pool)]
    return layout, THEME_KEYS[(did * 5 + len(layout)) % len(THEME_KEYS)]


def compatible(cover: dict, has_photo: bool) -> list[str]:
    """这篇的封面信息能用哪些版式(要点不够不能用清单类,没有数字不能用大数字,没有现成实拍图时只用纯文字版式,免得多花一张图)。"""
    n = len(cover.get("points") or [])
    ok = list(PHOTO_LAYOUTS) if has_photo else []
    ok += ["statement", "question"]
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
    opts = [x for x in compatible(cover, has_photo) if x != cover.get("layout")] or compatible(cover, has_photo)
    theme = r.choice([t for t in THEME_KEYS if t != cover.get("theme")] or THEME_KEYS)
    return r.choice(opts), theme


def cover_line(cc: dict) -> str:
    """写进 image_prompts[0] 的封面说明:给人看,也让合规扫描能扫到封面上的字。"""
    label, need, _ = cards.LAYOUTS[cc["layout"]]
    texts = [cc.get("title"), cc.get("sub"), *(cc.get("points") or []), cc.get("tag"), f"{cc.get('num') or ''}{cc.get('unit') or ''}"]
    return (f"封面·{label}:" + (f"实拍背景 {cc.get('scene') or '产品特写'} " if need else "纯文字封面 ")
            + "文字:" + "".join(f"「{t}」" for t in texts if t))


def photo_prompt(cc: dict, with_product: bool) -> str:
    head = "以参考图中的产品为主体,严格保持产品的外观、颜色、包装和包装上的文字不变。" if with_product else ""
    return (f"{head}{cc.get('scene') or '产品特写,干净的背景'}。{COMPOSE.get(cc.get('layout'), '主体居中')}。"
            f"真实摄影质感,3:4 竖构图。{NO_TEXT}。")


def inner_prompts(raw, k: int, track: str, did: int, title: str) -> list[str]:
    """内页配图提示词:去掉所有会显示成文字的「」内容,补齐/裁剪到 k 条,每条都要求画面无字。"""
    out = []
    for p in raw or []:
        t = re.sub(r"[「『“\"][^」』”\"]*[」』”\"]", "", str(p))
        t = re.sub(r"[,，;；]?\s*(画面中不要出现[^。,，;；]*|图中不要出现[^。,，;；]*)", "", t).strip(" ,，。;；")
        if t:
            out.append(f"{t},{NO_TEXT}")
    while len(out) < k:
        out.append(f"{title}的主体特写,{scene(track, did, len(out) + 1)},{NO_TEXT}")
    return out[:k]


def plan(did: int, out: dict, track: str, brand: str, n: int, safe=lambda t: t) -> tuple[list[str], dict]:
    """返回 (image_prompts, cover_card)。image_prompts[0] 是封面说明,其余是内页提示词,共 n 条。"""
    cc = clean_cover(out.get("cover"), out, safe)
    cc["brand"] = brand
    cc["layout"], cc["theme"] = choose(did, cc, out.get("title") or "")
    return [cover_line(cc), *inner_prompts(out.get("image_prompts"), n - 1, track, did, out.get("title") or "")], cc
