"""每日分发清单:今天该对谁做「全选 → 添加到共享相簿」,做过的不会重复提醒。

    python3 daily_report.py                       今天的清单(哪个人几张、有没有还没共享的)
    python3 daily_report.py --day 2026-11-13      看某一天
    python3 daily_report.py --done 张怡           这个人当天的相册已经共享完了,记下来(之后新增的才会再提醒)
    python3 daily_report.py --done-all            当天所有人都共享完了

操作(每个人约 10 秒):照片 App 侧边栏打开「人名文件夹 → 当天日期相册」→ ⌘A 全选 → 工具栏「共享」→「共享相簿」→
确认「实况照片」是勾上的 → 选这个人的共享相簿。做完用 --done 记一下,避免第二次又加一遍造成重复。
(共享相簿里不能用脚本删照片,重复加了要在相簿里手动删。)
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photos_lib as pl

STATE = Path.home() / ".livephoto_shared.json"   # {"张怡|2026-11-14": 12}:这个人这天已经共享了多少张


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    except Exception:
        return {}


def build(rows: list[dict], day: str, state: dict) -> list[dict]:
    """当天各人的清单:总张数、已共享张数、还差几张。"""
    out = []
    for r in sorted((x for x in rows if x["day"] == day), key=lambda x: x["who"]):
        done = int(state.get(f"{r['who']}|{day}", 0))
        out.append({"who": r["who"], "count": r["count"], "done": done, "todo": max(r["count"] - done, 0)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--done", metavar="名字")
    ap.add_argument("--done-all", action="store_true")
    a = ap.parse_args()
    rows = pl.list_person_albums()
    state = load_state()
    todo = build(rows, a.day, state)
    if a.done or a.done_all:
        hit = [t for t in todo if a.done_all or t["who"] == a.done]
        if not hit:
            return print(f"{a.day} 没有找到{'任何相册' if a.done_all else '「' + a.done + '」的相册'}。")
        for t in hit:
            state[f"{t['who']}|{a.day}"] = t["count"]
            print(f"✓ 已记下:{t['who']} {a.day} 共 {t['count']} 张都共享过了")
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        return
    if not todo:
        return print(f"{a.day} 照片 App 里还没有任何人的相册。")
    print(f"{a.day} 分发清单(照片 App → 人名文件夹 → {a.day} → ⌘A → 共享 → 共享相簿):\n")
    for t in todo:
        mark = "✓ 已共享" if t["todo"] == 0 else (f"新增 {t['todo']} 张没共享" if t["done"] else "还没共享")
        print(f"  {'○' if t['todo'] else '✓'} {t['who']:<10} 共 {t['count']:>3} 张   {mark}")
    left = [t["who"] for t in todo if t["todo"]]
    print(f"\n{'还要做:' + '、'.join(left) if left else '今天的都共享完了。'}")
    if left:
        print("做完一个就运行 python3 daily_report.py --done 名字 记一下。注意:相册里有新增的话,再全选会把已共享的也重复加一遍,"
              "这时只选新增的那几张(相册按添加时间排,新的在最后)。")


if __name__ == "__main__":
    main()
