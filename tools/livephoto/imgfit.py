"""任意比例的图 → 3:4(1080×1440)。只依赖 Pillow,视频 worker 和 Mac 端都用它。"""
from PIL import Image, ImageFilter

W, H = 1080, 1440   # 小红书 Live 图 3:4


def fit_to_3x4(img: Image.Image, mode: str = "crop", focus_y: float = 0.35) -> Image.Image:
    """成品库的图常常是 9:16 等比例,Live 图要 3:4(1080×1440)。
    crop = 铺满后裁掉多出来的部分(focus_y 决定保留偏上还是偏下);blur = 完整显示,上下用放大虚化的原图补边。"""
    img = img.convert("RGB")
    if abs(img.width / img.height - W / H) <= 0.01:
        return img.resize((W, H), Image.LANCZOS)
    k = max(W / img.width, H / img.height)             # 铺满
    cover = img.resize((max(W, round(img.width * k)), max(H, round(img.height * k))), Image.LANCZOS)
    x0 = (cover.width - W) // 2
    y0 = round((cover.height - H) * min(max(focus_y, 0), 1))
    bg = cover.crop((x0, y0, x0 + W, y0 + H))
    if mode != "blur":
        return bg
    bg = bg.filter(ImageFilter.GaussianBlur(40)).point(lambda v: int(v * 0.75))   # 虚化并压暗,让前景更突出
    k = min(W / img.width, H / img.height)             # 完整放进去
    fg = img.resize((round(img.width * k), round(img.height * k)), Image.LANCZOS)
    bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    return bg
