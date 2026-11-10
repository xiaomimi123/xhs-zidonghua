"""缩略图:卡片网格、列表里用小图,原图(1~3MB)只在点开预览时才加载。

成品库 / 任务 / 笔记库一屏几十张封面,原来都直接加载原图,局域网 / 手机上要下载几十 MB,页面才慢。
缩略图按需生成、落盘缓存(data/thumbs),文件名带原图的修改时间,原图换了会自动重新生成;
浏览器可以长期缓存(immutable)。只有 Pillow 能解码的 JPEG / PNG / WebP 才会生成,其余原样返回。"""
import os
import threading
import time
from pathlib import Path

from .db import DATA

DIR = DATA / "thumbs"
WIDTHS = (240, 360, 480, 720)   # 只允许这几档,避免有人用各种宽度把缓存目录撑满
QUALITY = 80
_OK_FORMATS = {"JPEG", "PNG", "WEBP"}


def pick_width(w: int) -> int:
    """取不小于 w 的最小一档(不够就取最大一档)。"""
    return next((x for x in WIDTHS if x >= max(1, int(w))), WIDTHS[-1])


def thumb_for(src: Path, w: int) -> Path:
    """返回缩略图路径(没有就现生成)。原图本来就比目标宽度小、格式不支持、或生成失败时,直接返回原图。"""
    from PIL import Image, ImageOps   # 延迟导入:没装 Pillow 时只是不出缩略图,不影响别的功能

    w = pick_width(w)
    st = src.stat()
    dst = DIR / f"{src.name}.{int(st.st_mtime)}.{w}.webp"
    if dst.is_file():
        return dst
    try:
        with Image.open(src) as im:
            if im.format not in _OK_FORMATS or im.width <= w or getattr(im, "is_animated", False):
                return src
            im = ImageOps.exif_transpose(im)   # 手机拍的照片带方向信息
            if im.mode in ("RGBA", "LA", "P"):
                bg = Image.new("RGB", im.size, (255, 255, 255))
                im = im.convert("RGBA")
                bg.paste(im, mask=im.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")
            im = im.resize((w, max(1, round(im.height * w / im.width))), Image.LANCZOS)
            DIR.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(f"{dst.name}.{os.getpid()}.{threading.get_ident()}.tmp")   # 先写临时文件再改名,别的请求不会读到半张图
            im.save(tmp, "WEBP", quality=QUALITY, method=4)
            os.replace(tmp, dst)
        return dst
    except Exception:
        return src


def warm(src: Path, widths=(360,)):
    """新图生成后顺手把常用缩略图做好,第一次打开列表就不用等。"""
    try:
        for w in widths:
            thumb_for(Path(src), w)
    except Exception:
        pass


def warm_dirs(dirs, widths=(360,), pause: float = 0.02):
    """服务启动后在后台补齐已有图片的缩略图(只补缺的,慢慢做,不占用请求)。"""
    def run():
        for d in dirs:
            try:
                files = sorted(Path(d).iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)   # 新图先做
            except OSError:
                continue
            for f in files:
                if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and f.is_file():
                    warm(f, widths)
                    time.sleep(pause)
    threading.Thread(target=run, daemon=True, name="thumb-warm").start()
