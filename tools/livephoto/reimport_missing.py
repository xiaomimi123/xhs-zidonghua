"""把漏导入照片 App 的 .pvt 补导一遍,并放进「发起人」文件夹里的「日期」相册。

用法:
    python3 reimport_missing.py --ids /tmp/failed_import.txt            # 按任务号清单补导
    python3 reimport_missing.py --scan ~/LivePhotos                     # 或扫整个目录(跳过照片 App 里已有的)
先 --dry-run 看清单,去掉 --dry-run 真正执行。导入过程会短暂把照片 App 弹到前台,导入完自动把焦点还回去。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mac_worker


def imported_ids() -> set[int]:
    """照片 App 里已有哪些 IMG_L<id>_LIVE(按文件名查,避免全库遍历)。"""
    out = subprocess.run(["osascript", "-e", '''
        tell application "Photos"
            set names to filename of every media item whose filename starts with "IMG_L"
        end tell
        return names'''], capture_output=True, text=True, timeout=300).stdout
    ids = set()
    for chunk in out.replace(", ", ",").split(","):
        if chunk.strip().startswith("IMG_L"):
            digits = "".join(c for c in chunk.split("_LIVE")[0] if c.isdigit())
            if digits:
                ids.add(int(digits))
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", help="任务号清单文件(每行一个),来自 worker 日志")
    ap.add_argument("--scan", type=lambda s: Path(s).expanduser(), help="扫描该目录下所有 .pvt 补导")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not (a.ids or a.scan):
        ap.error("要么 --ids 任务号清单,要么 --scan 目录")

    if a.scan:
        pkgs = sorted(a.scan.rglob("IMG_L*_LIVE.pvt"))
        jobs = [(int(p.name.split("_")[1][1:]), p) for p in pkgs]
    else:
        jobs = []
        for line in Path(a.ids).read_text().split():
            jid = line.strip()
            if not jid:
                continue
            hits = list(Path.home().joinpath("LivePhotos").rglob(f"IMG_L{jid}_LIVE.pvt"))
            if hits:
                jobs.append((int(jid), hits[0]))
            else:
                print(f"L{jid}: 磁盘上找不到包,跳过")

    have = imported_ids()
    todo = [(jid, p) for jid, p in jobs if jid not in have]
    print(f"共 {len(jobs)} 个,照片 App 里已有 {len(jobs) - len(todo)},待补导 {len(todo)} 个")
    if a.dry_run:
        for jid, p in todo[:20]:
            print(f"  L{jid} ← {p}")
        if len(todo) > 20:
            print(f"  …还有 {len(todo) - 20} 个")
        return

    ok = fail = 0
    t0 = time.time()
    for i, (jid, p) in enumerate(todo, 1):
        # 发起人和日期从路径拿:~/LivePhotos/发起人/日期/IMG_L.._LIVE.pvt(日期取路径里的任务日期,不是今天)
        who, day = (p.parts[-3], p.parts[-2]) if len(p.parts) >= 3 else ("", "")
        if mac_worker.import_to_photos(p, who, day):
            ok += 1
        else:
            fail += 1
            print(f"   L{jid} 失败")
        if i % 10 == 0:
            print(f"…进度 {i}/{len(todo)},已用时 {(time.time() - t0) / 60:.0f} 分钟")
    print(f"补导完成:成功 {ok},失败 {fail},总用时 {(time.time() - t0) / 60:.0f} 分钟")


if __name__ == "__main__":
    main()
