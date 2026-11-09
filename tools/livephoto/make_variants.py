"""排查 iCloud 不接受哪个结构:同一张封面,生成几个只在 MOV 元数据结构上有差别的版本。
    python make_variants.py 封面.png --out 输出目录
T0 = 只有内容 ID(ffmpeg 原样);T1 = 最初那版结构;T2 = 新轨道 + meta 留在 udta;T3 = 老轨道 + meta 挪到 moov;T4 = 现在这版(全按真品)"""
import argparse
import sys
import uuid
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
import make_live as ml  # noqa: E402

VARIANTS = {
    "T0": None,                                   # 不加静态帧轨道
    "T1": dict(move_meta=False, real_track=False),
    "T2": dict(move_meta=False, real_track=True),
    "T3": dict(move_meta=True, real_track=False),
    "T4": dict(move_meta=True, real_track=True),
}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    src = Image.open(a.src)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, opt in VARIANTS.items():
        mov = out / f"IMG_{name}_VAR.MOV"
        ml.render_mov(src, mov, str(uuid.uuid4()).upper(), 5, 30, 1.08, 0.35)
        if opt:
            ml.add_still_image_time(mov, **opt)
        print(name, mov.name, mov.stat().st_size, opt)
