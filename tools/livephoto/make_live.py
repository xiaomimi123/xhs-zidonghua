"""把一张 3:4 封面做成 iPhone 实况照片(Live Photo):关键帧 JPG + 5 秒 MOV,两边写入同一个内容 ID。

Windows 上没有 makelive / AVFoundation,这里直接按格式写元数据:
  - JPG:EXIF 里的 Apple MakerNote,tag 0x0011 = 内容 ID(iPhone 相册靠它配对)
  - MOV:moov/meta 里的 com.apple.quicktime.content.identifier(ffmpeg 写),
         再加一条 mebx 元数据轨,里面是 com.apple.quicktime.still-image-time(静态帧在第几秒)

    python make_live.py 封面.png --out 输出目录 --name SLUG [--seconds 5] [--fps 30] [--zoom 1.08] [--fit crop|blur]
    python make_live.py --check 输出目录/IMG_SLUG_LIVE.JPG 输出目录/IMG_SLUG_LIVE.MOV   # 只检查元数据
"""
import argparse
import struct
import subprocess
import sys
import uuid
from pathlib import Path

from PIL import Image
import imageio_ffmpeg

from imgfit import W, H, fit_to_3x4



# ---------------- 画面:缓慢推近(Ken Burns) ----------------
def frames(src: Image.Image, n: int, zoom: float, focus_y: float):
    """第 0 帧就是原封面(和关键帧一致),之后缓慢放大到 zoom 倍。focus_y = 放大时的视觉中心(0 顶部 ~ 1 底部)。"""
    big = src.resize((W * 2, H * 2), Image.LANCZOS)   # 先放大一倍再裁,缩放更平滑
    for i in range(n):
        t = i / max(n - 1, 1)
        e = t * t * (3 - 2 * t)                       # 缓入缓出
        s = 1 + (zoom - 1) * e
        cw, ch = W * 2 / s, H * 2 / s
        x0 = (W * 2 - cw) / 2
        y0 = (H * 2 - ch) * focus_y
        yield big.resize((W, H), Image.LANCZOS, box=(x0, y0, x0 + cw, y0 + ch)).tobytes()


def render_mov(src: Image.Image, mov: Path, asset_id: str, seconds: float, fps: int, zoom: float, focus_y: float):
    n = int(round(seconds * fps))
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
           "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p", "-preset", "slow", "-crf", "18", "-tag:v", "avc1",
           "-movflags", "use_metadata_tags", "-metadata", f"com.apple.quicktime.content.identifier={asset_id}",
           "-f", "mov", str(mov)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for fr in frames(src.convert("RGB"), n, zoom, focus_y):
        p.stdin.write(fr)
    p.stdin.close()
    if p.wait() != 0:
        raise SystemExit("ffmpeg 编码失败")


# ---------------- MOV:加一条 still-image-time 元数据轨 ----------------
def _box(typ: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + typ + payload


def _full(typ: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return _box(typ, bytes([version]) + flags.to_bytes(3, "big") + payload)


def _children(buf: bytes, start: int, end: int):
    """顶层 / 容器内的盒子:[(类型, 盒子起点, 盒子终点, 头长度)]"""
    out, i = [], start
    while i + 8 <= end:
        size, typ = struct.unpack(">I4s", buf[i:i + 8])
        hdr = 8
        if size == 1:
            size, hdr = struct.unpack(">Q", buf[i + 8:i + 16])[0], 16
        elif size == 0:
            size = end - i
        out.append((typ, i, i + size, hdr))
        i += size
    return out


def _metadata_trak(track_id: int, video_track_id: int, movie_ts: int, chunk_offset: int, real: bool = True) -> bytes:
    """一条 mebx 定时元数据轨:只有 1 个 9 字节样本(still-image-time = -1),占 1 个 600 时间单位,位于第 0 秒。
    结构照 iPhone 真品:tkhd 标志位 15、tref/cdsc 指向视频轨、gmhd/gmin 头、手柄 appl。"""
    key = b"com.apple.quicktime.still-image-time"
    keybox = _box(b"\x00\x00\x00", _box(b"keyd", b"mdta" + key) + _box(b"dtyp", struct.pack(">II", 0, 65)))   # 65 = 有符号 8 位整数
    mebx = _box(b"mebx", b"\x00" * 6 + struct.pack(">H", 1) + _box(b"keys", keybox))
    stbl = _box(b"stbl",
                _full(b"stsd", 0, 0, struct.pack(">I", 1) + mebx)
                + _full(b"stts", 0, 0, struct.pack(">III", 1, 1, 1))
                + _full(b"stsc", 0, 0, struct.pack(">IIII", 1, 1, 1, 1))
                + _full(b"stsz", 0, 0, struct.pack(">II", 9, 1))
                + _full(b"stco", 0, 0, struct.pack(">II", 1, chunk_offset)))
    gmhd = _box(b"gmhd", _box(b"gmin", b"\x00\x00\x00\x00" + struct.pack(">H", 0x40) + struct.pack(">HHH", 0x8000, 0x8000, 0x8000) + struct.pack(">HH", 0, 0)))
    minf = _box(b"minf", (gmhd if real else _full(b"nmhd", 0, 0, b""))   # real=False:最初那版用的 nmhd 头(排查用)
                + _full(b"hdlr", 0, 0, struct.pack(">I", 0) + b"alis" + b"\x00" * 12 + bytes([23]) + b"Core Media Data Handler")
                + _box(b"dinf", _full(b"dref", 0, 0, struct.pack(">I", 1) + _full(b"url ", 0, 1, b""))) + stbl)
    mdia = _box(b"mdia",
                _full(b"mdhd", 0, 0, struct.pack(">IIIIHH", 0, 0, 600, 1, 0x55C4, 0))
                + _full(b"hdlr", 0, 0, struct.pack(">I", 0) + b"meta" + b"appl" + struct.pack(">II", 1, 0) + bytes([19]) + b"Core Media Metadata") + minf)
    matrix = struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
    dur_mv = max(1, round(movie_ts / 600))   # 1 个 600 单位 = 多少个影片时间单位
    tkhd = _full(b"tkhd", 0, 15 if real else 1, struct.pack(">IIIII", 0, 0, track_id, 0, dur_mv) + b"\x00" * 8
                 + struct.pack(">hhhH", 0, 0, 0, 0) + matrix + struct.pack(">II", 0, 0))
    tref = _box(b"tref", _box(b"cdsc", struct.pack(">I", video_track_id)))
    return _box(b"trak", tkhd + (tref if real else b"") + mdia)


def add_still_image_time(mov: Path, move_meta: bool = True, real_track: bool = True):
    buf = mov.read_bytes()
    top = _children(buf, 0, len(buf))
    moov = next(b for b in top if b[0] == b"moov")
    if moov[2] != len(buf):
        raise SystemExit("moov 不在文件末尾,不能安全追加(ffmpeg 不要加 faststart)")
    kids = _children(buf, moov[1] + moov[3], moov[2])
    mv = next(k for k in kids if k[0] == b"mvhd")
    body = bytearray(buf[mv[1] + mv[3]:mv[2]])
    v1 = body[0] == 1
    timescale, duration = (struct.unpack(">IQ", body[20:32]) if v1 else struct.unpack(">II", body[12:20]))
    nt = 108 if v1 else 96
    next_id = struct.unpack(">I", body[nt:nt + 4])[0]
    body[nt:nt + 4] = struct.pack(">I", next_id + 1)
    # 内容 ID 所在的 meta(mdta keys + ilst):ffmpeg 放在 moov/udta 里,iPhone 真品放在 moov 下一层,挪到和真品一样的位置
    udta = next((k for k in kids if k[0] == b"udta"), None)
    movie_meta, udta_rest = b"", b""
    if udta and move_meta:
        for t, s, e, h in _children(buf, udta[1] + udta[3], udta[2]):
            if t == b"meta":
                movie_meta = buf[s:e]
            else:
                udta_rest += buf[s:e]
    rest = b"".join(buf[k[1]:k[2]] for k in kids if k[0] != b"mvhd" and not (k[0] == b"udta" and move_meta))
    if udta_rest:
        rest += _box(b"udta", udta_rest)
    mvhd = buf[mv[1]:mv[1] + mv[3]] + bytes(body)

    def build(off):   # 顺序和真品一样:mvhd、各轨道、最后是 meta
        return _box(b"moov", mvhd + rest + _metadata_trak(next_id, 1, timescale, off, real_track) + movie_meta)
    moov_len = len(build(0))
    sample = struct.pack(">I", 9) + b"\x00\x00\x00\x01" + b"\xff"      # still-image-time = -1:静态帧就是这一刻(第 0 秒)
    off = moov[1] + moov_len + 8
    mov.write_bytes(buf[:moov[1]] + build(off) + _box(b"mdat", sample))


# ---------------- JPG:EXIF 里写 Apple MakerNote ----------------
def maker_note(asset_id: str) -> bytes:
    """Apple iOS MakerNote:头 14 字节 + 一个 IFD,只放 0x0011 内容 ID;偏移从 MakerNote 起点算。"""
    val = asset_id.encode() + b"\x00"
    ifd = struct.pack(">H", 1) + struct.pack(">HHII", 0x0011, 2, len(val), 14 + 2 + 12 + 4) + struct.pack(">I", 0)
    return b"Apple iOS\x00" + b"\x00\x01" + b"MM" + ifd + val


def exif_app1(asset_id: str) -> bytes:
    mn = maker_note(asset_id)
    ifd0 = struct.pack(">H", 1) + struct.pack(">HHII", 0x8769, 4, 1, 26) + struct.pack(">I", 0)              # → Exif IFD 在 26
    exif = (struct.pack(">H", 2) + struct.pack(">HHI", 0x9000, 7, 4) + b"0232"                              # ExifVersion
            + struct.pack(">HHII", 0x927C, 7, len(mn), 26 + 2 + 24 + 4) + struct.pack(">I", 0))            # MakerNote 在 56
    tiff = b"MM\x00\x2a" + struct.pack(">I", 8) + ifd0 + exif + mn
    payload = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def write_jpg(src: Image.Image, jpg: Path, asset_id: str):
    tmp = jpg.with_suffix(".tmp.jpg")
    src.convert("RGB").resize((W, H), Image.LANCZOS).save(tmp, "JPEG", quality=95, subsampling=0)
    data = tmp.read_bytes()
    tmp.unlink()
    i = 2
    while data[i:i + 2] == b"\xff\xe0":   # 去掉 JFIF APP0,让 EXIF 紧跟在文件头后面
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    jpg.write_bytes(b"\xff\xd8" + exif_app1(asset_id) + data[i:])


# ---------------- 检查 ----------------
def check(jpg: Path, mov: Path) -> bool:
    ok = True
    mn = Image.open(jpg).getexif().get_ifd(0x8769).get(0x927C, b"")
    jid = mn[32:].split(b"\x00")[0].decode() if mn.startswith(b"Apple iOS\x00") else ""
    buf = mov.read_bytes()
    i = buf.find(b"com.apple.quicktime.content.identifier")
    j = buf.find(b"data", i)
    mid = ""
    if i > 0 and j > 0:   # ilst 里的 data 盒子:类型(4) + 语言(4) 之后是 UTF-8 值
        size = struct.unpack(">I", buf[j - 4:j])[0]
        mid = buf[j + 12:j - 4 + size].decode(errors="replace")
    still = b"com.apple.quicktime.still-image-time" in buf and b"mebx" in buf
    print(f"JPG 内容 ID : {jid or '没有'}")
    print(f"MOV 内容 ID : {mid or '没有'}")
    print(f"MOV 静态帧轨: {'有' if still else '没有'}")
    print(f"尺寸        : JPG {Image.open(jpg).size}")
    if not jid or jid != mid or not still:
        ok = False
    print("结论        :", "两边 ID 一致,元数据齐全" if ok else "有问题")
    return ok


def build_pair(src: Path, out: Path, slug: str, seconds: float = 5, fps: int = 30, zoom: float = 1.08,
               focus_y: float = 0.35, fit: str = "crop") -> tuple[Path, Path, str]:
    """一张图 → 关键帧 JPG + 5 秒 MOV(两边同一个内容 ID,MOV 里带 still-image-time 轨)。返回 (jpg, mov, 内容 ID)。"""
    out.mkdir(parents=True, exist_ok=True)
    img = fit_to_3x4(Image.open(src), fit, focus_y)
    asset_id = str(uuid.uuid4()).upper()
    jpg, mov = out / f"IMG_{slug}_LIVE.JPG", out / f"IMG_{slug}_LIVE.MOV"
    write_jpg(img, jpg, asset_id)
    render_mov(img, mov, asset_id, seconds, fps, zoom, focus_y)
    add_still_image_time(mov)
    return jpg, mov, asset_id


def build_pair_from_video(video: Path, still: Path | None, out: Path, slug: str, fit: str = "crop", focus_y: float = 0.5,
                          keep_audio: bool = False, sharpen: float = 0.6) -> tuple[Path, Path, str]:
    """以一条现成的视频(例如 H3 生成的手机随手拍风格短视频)为源做实况:关键帧 JPG + MOV,两边同一个内容 ID。
    关键帧 = 原图(still,没有就取视频第一帧),MOV 里带 still-image-time 轨。视频缩放 / 裁成 1080×1440;默认去掉声音。"""
    out.mkdir(parents=True, exist_ok=True)
    asset_id = str(uuid.uuid4()).upper()
    jpg, mov = out / f"IMG_{slug}_LIVE.JPG", out / f"IMG_{slug}_LIVE.MOV"
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    if still and Path(still).is_file():
        img = fit_to_3x4(Image.open(still), fit, focus_y)
    else:   # 没有原图:取视频第一帧
        tmp = out / f".first_{slug}.png"
        subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-frames:v", "1", str(tmp)], check=True)
        img = fit_to_3x4(Image.open(tmp), "crop", focus_y)
        tmp.unlink(missing_ok=True)
    write_jpg(img, jpg, asset_id)
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-map_metadata", "-1", "-map", "0:v:0"]
    cmd += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "128k"] if keep_audio else ["-an"]
    # AI 生成的视频细节偏软,放大后更糊;轻锐化(亮度通道)能让清晰度接近原图。太强会出白边,0.6 左右合适,0 = 不锐化
    vf = f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1" + (f",unsharp=5:5:{sharpen}:5:5:0.0" if sharpen > 0 else "")
    cmd += ["-vf", vf,
            "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p", "-preset", "slow", "-crf", "17", "-tag:v", "avc1",
            "-movflags", "use_metadata_tags", "-metadata", f"com.apple.quicktime.content.identifier={asset_id}", "-f", "mov", str(mov)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 转码失败:{r.stderr.strip()[-300:]}")
    add_still_image_time(mov)
    return jpg, mov, asset_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?")
    ap.add_argument("--out", default=".")
    ap.add_argument("--name", default="")
    ap.add_argument("--seconds", type=float, default=5)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--zoom", type=float, default=1.08)
    ap.add_argument("--focus-y", type=float, default=0.35)
    ap.add_argument("--fit", choices=("crop", "blur"), default="crop", help="图不是 3:4 时:crop 裁切 / blur 完整显示+虚化补边")
    ap.add_argument("--video", help="以这条视频为源(不做推近动画);src 当作关键帧原图,可以省略")
    ap.add_argument("--keep-audio", action="store_true", help="用 --video 时保留声音(默认去掉)")
    ap.add_argument("--sharpen", type=float, default=0.6, help="用 --video 时的锐化强度,0 = 不锐化,超过 1 容易出白边")
    ap.add_argument("--check", nargs=2, metavar=("JPG", "MOV"))
    a = ap.parse_args()
    if a.check:
        sys.exit(0 if check(Path(a.check[0]), Path(a.check[1])) else 1)
    out = Path(a.out)
    slug = a.name or (Path(a.src).stem if a.src else "")
    if a.video:
        jpg, mov, asset_id = build_pair_from_video(Path(a.video), Path(a.src) if a.src else None, out, slug or Path(a.video).stem, a.fit, a.focus_y, a.keep_audio, a.sharpen)
    else:
        jpg, mov, asset_id = build_pair(Path(a.src), out, slug, a.seconds, a.fps, a.zoom, a.focus_y, a.fit)
    print(f"内容 ID: {asset_id}")
    print(jpg)
    print(mov)
    check(jpg, mov)


if __name__ == "__main__":
    main()
