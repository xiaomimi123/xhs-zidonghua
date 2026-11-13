"""上传产品时的素材检查:提交前把常见的问题提醒出来(传错图、混进别的产品、文案像指令、太长被截断…)。

只提醒,不阻止:每条提醒页面上让用户选「仍然继续」或「回去修改」。素材脏了,后面的笔记和配图都会跟着跑偏
(比如姜黄粉的参考图里混进了肉桂粉的罐子,生成的图里就会出现肉桂),所以要在上传这一步拦住。"""
import hashlib
import json
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path

from . import llm, products
from .db import q_all

# 像是写给 AI 的指令(从别处复制来的、没删干净)
INSTRUCTION = re.compile(r"帮我|请你|你帮|给我|替我|收集一些|生成一|写一篇|麻烦你|能不能|可以帮|请帮")
FORMAT_MARKS = re.compile(r"[#*✅❌▶●■◆]{1,}")
REFS_USED = 3            # 生图时实际送去当参考的张数(管理端 product_max_refs 的默认值)
LIMITS = {"selling_points": 500, "audience": 100, "forbid": 200}
LOW_RES = 500            # 短边小于这个像素,包装上的字生图时容易糊
VISION_TIMEOUT = 45      # 看图检查最多等这么久;超时就跳过,不拖住提交
VISION_MAX_IMAGES = 6

_HASH: dict[tuple[str, int], str] = {}


def _md5_file(p: Path) -> str:
    key = (p.name, p.stat().st_mtime_ns)
    if key not in _HASH:
        _HASH[key] = hashlib.md5(p.read_bytes()).hexdigest()
    return _HASH[key]


def _other_images(exclude_pid: int) -> dict[str, tuple[str, str]]:
    """别的产品里已有的图 {md5: (产品名, 文件名)}。"""
    out = {}
    for r in q_all("SELECT id, name, images FROM products WHERE id!=?", exclude_pid or 0):
        for f in r["images"] or []:
            p = products.DIR / f
            if p.is_file():
                out.setdefault(_md5_file(p), (r["name"], f))
    return out


def _core(word: str) -> str:
    """品类词去掉末尾的通用字,用来在文案里找:肉桂粉 → 肉桂,豆腐乳 → 豆腐(太短的不要)。"""
    w = (word or "").strip()
    if len(w) >= 3 and w[-1] in "粉酱乳脯干茶油醋盐饼糖":
        w = w[:-1]
    return w if len(w) >= 2 else ""


def _text_warnings(name: str, selling_points: str, audience: str, forbid: str, exclude_pid: int, category: str = "") -> list[dict]:
    out = []
    text = f"{selling_points}\n{audience}\n{forbid}"
    own = f"{name}{category}"
    # 文案里出现了产品库里另一个产品的关键词:多半是从那个产品复制过来的
    seen_core = set()
    for r in q_all("SELECT name, category FROM products WHERE id!=? ORDER BY id DESC", exclude_pid or 0):
        core = _core(r["category"])
        if core and core in text and core not in own and core not in seen_core:
            seen_core.add(core)
            out.append({"level": "warn", "where": "文案", "msg": f"文案里提到了另一个产品「{r['name']}」的关键词「{core}」。如果这段文案是从那个产品复制来的,请改成这个产品自己的;只是写搭配用法可以忽略"})
    for label, val in (("卖点", selling_points), ("适合人群", audience), ("不能出现的说法", forbid)):
        m = INSTRUCTION.search(val or "")
        if m:
            out.append({"level": "warn", "where": "文案", "msg": f"「{label}」里有一句像是给 AI 的指令(「{(val or '')[max(0, m.start() - 4):m.end() + 10].strip()}…」),会被原样当成产品资料。这里只写事实本身,例如适合人群写「上班族、控糖人群」"})
    for key, label in (("selling_points", "卖点"), ("audience", "适合人群"), ("forbid", "不能出现的说法")):
        val = {"selling_points": selling_points, "audience": audience, "forbid": forbid}[key]
        if len((val or "").strip()) > LIMITS[key]:
            out.append({"level": "warn", "where": "文案", "msg": f"「{label}」有 {len(val.strip())} 字,超过 {LIMITS[key]} 字的部分会被截掉,请把最重要的放前面、精简一下"})
    if not (selling_points or "").strip():
        out.append({"level": "tip", "where": "文案", "msg": "没有填卖点:文案只能凭产品图推断。建议写 3-5 条短句,只写这个产品"})
    elif FORMAT_MARKS.search(selling_points):
        out.append({"level": "tip", "where": "文案", "msg": "卖点里有 # * ✅ 这类格式符号(像是从别处复制来的),建议整理成几条纯文字短句"})
    if len((name or "").strip()) > 20:
        out.append({"level": "tip", "where": "文案", "msg": "产品名称偏长,建议只写「品牌 + 品类」,如「国圣米酱豆腐乳」"})
    return out


def ordered(new: list[tuple[bytes, str]], keep: list[str], order: list[str] | None = None) -> list[tuple[str, str | None, bytes | None]]:
    """按页面上的顺序排好的图片 [(类型, 原图文件名, 新图字节)],类型 k = 已有的原图,a = 新上传的。
    order = 页面传来的顺序 ["k:文件名", "a:序号"];没传就是原图在前、新图在后;order 里没提到的图补在最后,不会丢。"""
    keep = [Path(f).name for f in keep]
    seq, used = [], set()
    for t in order or []:
        kind, _, v = str(t).partition(":")
        if kind == "k" and Path(v).name in keep and ("k", Path(v).name) not in used:
            used.add(("k", Path(v).name)); seq.append(("k", Path(v).name, None))
        elif kind == "a" and v.isdigit() and int(v) < len(new) and ("a", int(v)) not in used:
            used.add(("a", int(v))); seq.append(("a", None, new[int(v)][0]))
    for f in keep:
        if ("k", f) not in used:
            seq.append(("k", f, None))
    for n, (b, _e) in enumerate(new):
        if ("a", n) not in used:
            seq.append(("a", None, b))
    return seq


def _bytes_of(item) -> bytes | None:
    kind, name, data = item
    if kind == "a":
        return data
    p = products.DIR / name
    return p.read_bytes() if p.is_file() else None


def _image_warnings(seq: list, exclude_pid: int) -> list[dict]:
    out = []
    total = len(seq)
    if total > REFS_USED:
        out.append({"level": "tip", "where": "图片", "msg": f"共 {total} 张图,生图时只会取前 {REFS_USED} 张当参考。请把最能代表产品的(包装正面、开盖实物)放在最前面(可以拖动或用 ‹ › 调整顺序)"})
    other = _other_images(exclude_pid)
    seen: dict[str, int] = {}
    blobs = []
    for i, item in enumerate(seq, 1):
        b = _bytes_of(item)
        blobs.append(b)
        if b is None:
            continue
        h = hashlib.md5(b).hexdigest()
        if h in other:
            out.append({"level": "warn", "where": "图片", "msg": f"第 {i} 张图和产品「{other[h][0]}」里的图完全一样,是不是传错了?放错产品会让生成的图里出现别的产品"})
        elif h in seen:
            out.append({"level": "warn", "where": "图片", "msg": f"第 {i} 张图和第 {seen[h]} 张重复了,可以去掉一张"})
        seen.setdefault(h, i)
    try:   # 分辨率偏低:没装 Pillow(本地开发)就跳过
        import io
        from PIL import Image
        for i, b in enumerate(blobs, 1):
            if b is None:
                continue
            with Image.open(io.BytesIO(b)) as im:
                if min(im.size) < LOW_RES:
                    out.append({"level": "tip", "where": "图片", "msg": f"第 {i} 张图分辨率偏低({im.size[0]}×{im.size[1]}),包装上的字生图时容易糊,建议用原图"})
    except Exception:
        pass
    return out


CHECK_SYSTEM = """你是产品素材审核员。用户为一个产品上传了几张图,请逐张判断每张图是否属于这个产品(包括它的包装、内容物、手持、使用或食用场景)。
指出明显不属于的:是别的品牌或别的品类的产品、与产品无关的图(截图、海报文案、二维码、表情包、大段文字)。同一张图里同时出现本产品和别的产品,也要指出。
没有把握的不要硬说是问题。另外判断第 1 张图是不是「干净背景(最好是白底)、产品完整露出的包装正面图」:是写 true;不是写 false 并在 first_reason 里一句话说明(如「是手持近景,看不到完整包装」)。
只输出 JSON:{"issues": [{"index": 图片序号(从 1 开始,按给你的顺序), "reason": "一句话,不超过 30 字"}], "first_ok": true, "first_reason": ""};没有问题 issues 就给空数组。"""


def _vision_warnings(name: str, paths: list[Path], category: str = "") -> list[dict]:
    """用看图模型判断有没有传错的图。没配看图接口 / 出错 / 超时都当没发现问题,不拦着用户。"""
    if not paths:
        return []
    ask = f"产品名称:{name}{('(品类:' + category + ')') if category else ''}\n一共 {len(paths[:VISION_MAX_IMAGES])} 张图,请逐张判断。"
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(lambda: llm._json_in(llm.vision_chat(CHECK_SYSTEM, ask, paths[:VISION_MAX_IMAGES])))
        data = fut.result(timeout=VISION_TIMEOUT)
    except (FutureTimeout, Exception):
        return []
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    out = []
    for it in data.get("issues") or []:
        try:
            idx = int(it.get("index"))
        except (TypeError, ValueError, AttributeError):
            continue
        if 1 <= idx <= len(paths):
            out.append({"level": "warn", "where": "图片", "msg": f"第 {idx} 张图可能不是这个产品:{str(it.get('reason') or '').strip()[:40] or '和产品对不上'}"})
    if data.get("first_ok") is False:   # 生图时第 1 张最重要:包装文字和产品大小比例都靠它
        out.append({"level": "tip", "where": "图片", "msg": f"第 1 张最好是白底、产品完整的包装正面图(它决定生成图里的包装文字和产品形状):{str(data.get('first_reason') or '').strip()[:40] or '当前这张不太符合'}。可以拖动换一张到最前面"})
    return out


def check(name: str, new_images: list[tuple[bytes, str]] | None = None, keep: list[str] | None = None, selling_points: str = "",
          audience: str = "", forbid: str = "", exclude_pid: int = 0, vision: bool = True, order: list[str] | None = None) -> list[dict]:
    """返回提醒列表 [{level: warn / tip, where: 图片 / 文案, msg}],warn 在前。图片序号和页面上的顺序一致(order 没传就是原图在前、新图在后)。"""
    new, keep = list(new_images or []), [Path(f).name for f in (keep or [])]
    seq = ordered(new, keep, order)
    cat = ""
    if exclude_pid:
        cat = (products.get(exclude_pid) or {}).get("category") or ""
    out = _image_warnings(seq, exclude_pid) + _text_warnings(name, selling_points, audience, forbid, exclude_pid, cat)
    if vision and seq:
        with tempfile.TemporaryDirectory() as td:
            paths = []
            for n, item in enumerate(seq):
                if item[0] == "k":
                    if (products.DIR / item[1]).is_file():
                        paths.append(products.DIR / item[1])
                else:
                    ext = next((e for b_, e in new if b_ is item[2]), "jpg")
                    pth = Path(td) / f"n{n}.{ext}"
                    pth.write_bytes(item[2])
                    paths.append(pth)
            out += _vision_warnings(name, paths, cat)
    out.sort(key=lambda w: 0 if w["level"] == "warn" else 1)
    return out
