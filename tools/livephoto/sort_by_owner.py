"""把 ~/LivePhotos 根目录下的旧 .pvt 按任务发起人归入子文件夹。

新版 mac_worker.py 直接把成品存到 ~/LivePhotos/发起人/;这个脚本整理它之前散在根目录的旧成品。
发起人从工作台的 /api/live/worker/owners 接口拿(任务号 → 发起人);拿不到的留在原地,不乱猜。

    python3 sort_by_owner.py --server http://192.168.0.41:18765 --token 采集端Token
    python3 sort_by_owner.py --server ... --token ... --root ~/LivePhotos --dry-run   # 先看会怎么动,不真移
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import time
import urllib.request
from pathlib import Path


def owners(server: str, token: str) -> dict[int, str]:
    req = urllib.request.Request(f"{server.rstrip('/')}/api/live/worker/owners",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return {int(k): v for k, v in json.loads(r.read()).items()}


def main():
    ap = argparse.ArgumentParser(description="旧 .pvt 按发起人归类")
    ap.add_argument("--server", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--root", type=lambda s: Path(s).expanduser(), default=Path("~/LivePhotos").expanduser())
    ap.add_argument("--dry-run", action="store_true", help="只打印要做什么,不真的移动")
    ap.add_argument("--by-date", action="store_true", help="在发起人下再按日期分层(取文件修改时间;新流程 worker 已自动按任务日期分,这主要整理旧包)")
    a = ap.parse_args()

    table = owners(a.server, a.token)
    moved = 0
    for pvt in sorted(a.root.glob("IMG_L*_LIVE.pvt")):
        m = re.search(r"IMG_L(\d+)_LIVE", pvt.name)
        who = table.get(int(m.group(1))) if m else None
        if not who:
            print(f"跳过 {pvt.name}(工作台上查不到发起人)")
            continue
        day = time.strftime("%Y-%m-%d", time.localtime(pvt.stat().st_mtime)) if a.by_date else ""
        dst = a.root / who / (day or "") / pvt.name if day else a.root / who / pvt.name
        if dst.exists():
            print(f"跳过 {pvt.name}(目标 {who}/{day or ''}/ 里已有)")
            continue
        print(f"{pvt.name} → {who}/{day or ''}/")
        if not a.dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(pvt), str(dst))
        moved += 1
    print(f"{'(dry-run) ' if a.dry_run else ''}共 {moved} 个待移动")


if __name__ == "__main__":
    main()
