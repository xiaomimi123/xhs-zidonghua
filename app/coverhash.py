"""封面图指纹:发布时员工常改标题和正文,但封面通常还是系统生成的那张。
把我们稿子的封面和千帆笔记的封面各算一个指纹,足够接近就认为是同一张图,用来把已发布的笔记对上稿子。

指纹有两部分:
- dh:256 位 dHash,只用来快速筛候选(汉明距离小才值得细比);
- vec:48×64 灰度缩略图,去掉整体亮度和对比度(减均值、除标准差)后量化成 int8,用来细比。
  细比用逐像素平均绝对差(单位:标准差)。实测(400 张真实封面):同一张图经过缩放 + JPEG 压缩 + 亮度对比度各变 4%,
  距离最大 0.08、99.8% 在 0.04 以内;不同稿子的封面最近也在 0.04 以上,绝大多数版式间隔 4~47 倍。
  文字卡片类封面(同版式、只有文字不同)间隔最小,所以对号时还要求「明显优于第二名」。
只用 Pillow。"""
import io

from PIL import Image

DH = 16                    # dHash 边长:16×16 = 256 位
TW, TH = 48, 64            # 细比用的缩略图尺寸
Q = 24                     # 量化倍数:z 值 × Q 取整,限制在 int8 范围
DH_MAX = 40                # 粗筛:dHash 汉明距离超过这个就不细比(同一张图正常发布最大 16,留出裁剪 / 调色的余量)
SAME = 0.03                # 细比距离不超过这个才算「同一张图」(实测同图 99.8% ≤ 0.04,取更严的 0.03,配合第二名间隔防误对)
LOOSE = 0.12               # 手动绑定窗口里,不超过这个就当候选列出来(给人挑)


def _dhash(img: Image.Image) -> int:
    g = img.convert("L").resize((DH + 1, DH), Image.LANCZOS)
    px = list(g.getdata())
    v = 0
    for y in range(DH):
        row = px[y * (DH + 1):(y + 1) * (DH + 1)]
        for x in range(DH):
            v = (v << 1) | (1 if row[x] > row[x + 1] else 0)
    return v


def _vec(img: Image.Image) -> bytes:
    v = list(img.convert("L").resize((TW, TH), Image.BOX).getdata())
    m = sum(v) / len(v)
    sd = (sum((x - m) ** 2 for x in v) / len(v)) ** .5 or 1.0
    return bytes((max(-127, min(127, round((x - m) / sd * Q))) & 0xFF) for x in v)


def fingerprint(img: Image.Image) -> tuple[int, bytes]:
    return _dhash(img), _vec(img)


def fp_bytes(b: bytes) -> tuple[int, bytes] | None:
    try:
        with Image.open(io.BytesIO(b)) as im:
            im.load()
            return fingerprint(im)
    except Exception:
        return None


def fp_file(path) -> tuple[int, bytes] | None:
    try:
        with Image.open(path) as im:
            im.load()
            return fingerprint(im)
    except Exception:
        return None


def dh_dist(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def vec_dist(a: bytes, b: bytes) -> float:
    """两个缩略图的平均绝对差(单位:标准差)。"""
    if len(a) != len(b) or not a:
        return 99.0
    s = 0
    for x, y in zip(a, b):
        s += abs(((x + 128) & 0xFF) - ((y + 128) & 0xFF))   # 还原成有符号再相减:(x^0x80) 等价,这里用平移避免负数
    return s / len(a) / Q


def to_hex(v: int) -> str:
    return f"{v:0{DH * DH // 4}x}"


def from_hex(s: str) -> int | None:
    try:
        return int(s, 16) if s else None
    except ValueError:
        return None
