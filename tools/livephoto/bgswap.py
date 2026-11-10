"""换背景的合成:把「原图里的主体」按掩膜贴回 AI 改过背景的新图上,保证主体(手、产品、包装文字)一像素不变。
只依赖 Pillow,视频 worker 用。主体掩膜由本机 ComfyUI 的 SAM3(文字提示抠图)给出,见 video_worker.py。

为什么要贴回:生图模型改背景时会顺手改主体(包装上的字、位置、大小),直接用它的结果主体不可靠;
把原图主体贴回去,背景是新的、主体是原来的,光线 / 阴影也比「纯背景 + 贴主体」自然得多。"""
from __future__ import annotations

from collections import deque

from PIL import Image, ImageChops, ImageFilter


def fill_holes(m: Image.Image, small_w: int = 320) -> Image.Image:
    """主体掩膜里被围住的空洞(杯子里的牛奶 / 粉末、罐口里面)也算主体:
    在低分辨率上从四周往里找「够得着的背景」,够不着的就是内部。只补内部空洞,不改主体边缘。"""
    m = m.convert("L").point(lambda v: 255 if v > 127 else 0)
    w, h = m.size
    sw = min(small_w, w)
    sh = max(1, round(h * sw / w))
    s = m.resize((sw, sh), Image.BILINEAR).point(lambda v: 255 if v > 100 else 0)
    px = bytearray(s.tobytes())
    seen = bytearray(sw * sh)
    q: deque = deque()

    def push(x: int, y: int):
        i = y * sw + x
        if not px[i] and not seen[i]:
            seen[i] = 1
            q.append((x, y))

    for x in range(sw):
        push(x, 0)
        push(x, sh - 1)
    for y in range(sh):
        push(0, y)
        push(sw - 1, y)
    while q:
        x, y = q.popleft()
        if x > 0:
            push(x - 1, y)
        if x < sw - 1:
            push(x + 1, y)
        if y > 0:
            push(x, y - 1)
        if y < sh - 1:
            push(x, y + 1)
    inside = Image.frombytes("L", (sw, sh), bytes(0 if seen[i] else 255 for i in range(sw * sh)))   # 够不着的 = 主体(含内部空洞)
    holes = ImageChops.subtract(inside, s).resize((w, h), Image.BILINEAR).point(lambda v: 255 if v > 60 else 0)   # 只取「内部空洞」
    return ImageChops.lighter(m, holes)


def clean_mask(m: Image.Image, close: int = 9, feather: float = 2.5) -> Image.Image:
    """填洞 + 闭运算(补细缝)+ 轻微羽化(贴回时边缘不生硬)。"""
    f = fill_holes(m).filter(ImageFilter.MaxFilter(close)).filter(ImageFilter.MinFilter(close))
    return f.filter(ImageFilter.GaussianBlur(feather))


def subject_from_background(bg_mask: Image.Image, erode: int = 3, feather: float = 2.5) -> Image.Image:
    """背景掩膜 → 主体掩膜(取补集):图里不属于背景的一切都是主体。
    轻微内缩(MinFilter)是为了不把主体边缘一圈原背景色带进新背景,再羽化让边缘不生硬。"""
    from PIL import ImageOps
    subj = ImageOps.invert(bg_mask.convert("L").point(lambda v: 255 if v > 127 else 0))
    if erode > 1:
        subj = subj.filter(ImageFilter.MinFilter(erode))
    return subj.filter(ImageFilter.GaussianBlur(feather))


def composite(orig: Image.Image, newbg: Image.Image, mask: Image.Image) -> Image.Image:
    """主体取原图,其余取新背景(缩放到和原图一样大)。"""
    orig = orig.convert("RGB")
    return Image.composite(orig, newbg.convert("RGB").resize(orig.size, Image.LANCZOS), mask.convert("L").resize(orig.size))


def coverage(mask: Image.Image) -> float:
    """掩膜前景占整张图的比例,用来判断抠图是否正常(太小 = 没抠到主体;太大 = 把背景也抠进来了)。"""
    h = mask.convert("L").histogram()
    return sum(h[128:]) / max(1, sum(h))
