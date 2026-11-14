"""把照片 App 里旧的「张怡 · 2026-11-14」平铺相册,整理成「张怡」文件夹 → 「2026-11-14」相册。

    python3 organize_photos.py --selftest         先试一下:建个临时文件夹和相册再删掉,确认脚本命令能用
    python3 organize_photos.py                    只看计划(不改任何东西):哪些相册要搬、哪些是空的
    python3 organize_photos.py --apply            真正整理:照片加进新相册,旧相册删掉(只删相册,照片本身不动)

整理前建议先在照片 App 里确认「iCloud 照片」已同步完;照片只是被加进新相册,不会被复制或删除。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photos_lib as pl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正整理(默认只看计划)")
    ap.add_argument("--selftest", action="store_true", help="自检:确认文件夹 / 相册命令能用")
    a = ap.parse_args()
    if a.selftest:
        return pl.selftest()
    albums = pl.list_top_albums()
    plan = []
    for al in albums:
        m = pl.OLD_RE.match(al["name"])
        if m:
            plan.append((al["name"], m.group(1).strip(), m.group(2), al["count"]))
    if not plan:
        print(f"顶层相册 {len(albums)} 个,没有「姓名 · 日期」格式的旧相册,不用整理。")
        return
    empty = [p for p in plan if p[3] == 0]
    print(f"找到 {len(plan)} 个旧相册(其中 {len(empty)} 个是空的),涉及 {len({p[1] for p in plan})} 个人:")
    for name, who, day, n in sorted(plan, key=lambda p: (p[1], p[2])):
        print(f"  {name}  ({n} 张)  →  {who} / {day}" + ("  [空相册,直接删]" if n == 0 else ""))
    if not a.apply:
        print("\n这只是计划。确认没问题后加 --apply 再运行一次。")
        return
    ok = fail = 0
    for name, who, day, n in plan:
        good, msg = pl.move_old_album(name, who, day)
        print(f"  {'✓' if good else '✗'} {name}" + ("" if good else f":{msg}"))
        ok += good
        fail += not good
    print(f"\n完成:成功 {ok},失败 {fail}。" + ("失败的旧相册还在,照片没丢,可以把上面的 ✗ 行发给我。" if fail else "照片 App 侧边栏现在是「人名文件夹 → 日期相册」。"))


if __name__ == "__main__":
    main()
