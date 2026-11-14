"""共享相簿自动加入:一次性设置 + 检查。

背景:「照片」的脚本接口不能操作共享相簿,所以 mac_worker 导入成功后,先把图放进「待分享」文件夹里这个人的暂存相册,
再运行这个人的快捷指令「加入共享-张怡」,由快捷指令把它加进她的共享相簿。每个人要建一个快捷指令(见下面的配方)。

    python3 share_setup.py --people 张怡 润娜 丽敏     建好这几个人的暂存相册,并打印每人快捷指令的配方
    python3 share_setup.py --from-server http://工作台IP:18765 --token Token   从工作台读出所有发起人,同上
    python3 share_setup.py --check                       检查:每个人的暂存相册、快捷指令是不是都有了
    python3 share_setup.py --test 张怡                   把暂存相册里最新一张通过快捷指令加进共享相簿(会真的加一张,试完去共享相簿里看)
可选 --flat:暂存相册改用顶层相册「待分享-张怡」(快捷指令里看不到「待分享」文件夹里的相册时用;worker 也要加 --share-flat)。

快捷指令配方(每人一个,可以建好一个后「复制」再改两处):
  名称:  加入共享-张怡
  动作1: 查找照片 —— 筛选「相册 是 待分享 / 张怡」,排序「添加日期 · 最新在前」,限制 1 张
  动作2: 添加到共享相簿 —— 选「张怡」那个共享相簿
共享相簿本身要先在「照片」里建好并邀请好同事(侧边栏「共享相簿」旁的 + ,在「邀请他人」框里输 Apple ID 邮箱 / 手机号回车)。
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photos_lib as pl


def people_from_server(server: str, token: str) -> list[str]:
    req = urllib.request.Request(server.rstrip("/") + "/api/live/worker/owners", headers={"Authorization": f"Bearer {token}"})
    data = json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8"))
    return sorted({v for v in (data.values() if isinstance(data, dict) else data) if v and v != pl.NO_OWNER})


def recipe(who: str, flat: bool) -> str:
    album = f"{pl.STAGE_FOLDER}-{who}" if flat else f"{pl.STAGE_FOLDER} / {who}"
    return (f"  快捷指令「{pl.shortcut_name(who)}」\n    ① 查找照片:相册 是「{album}」,排序「添加日期·最新在前」,限制 1\n    ② 添加到共享相簿:选「{who}」那个共享相簿")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--people", nargs="+", help="发起人名字(和工作台任务页里的发起人一致)")
    ap.add_argument("--from-server", metavar="工作台地址")
    ap.add_argument("--token")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--test", metavar="名字")
    ap.add_argument("--flat", action="store_true")
    a = ap.parse_args()
    people = a.people or []
    if a.from_server:
        if not a.token:
            ap.error("--from-server 要带 --token")
        people += [x for x in people_from_server(a.from_server, a.token) if x not in people]
    if a.test:
        who = pl.clean_person(a.test)
        print(f"测试:把「{who}」暂存相册里最新一张通过「{pl.shortcut_name(who)}」加进共享相簿…")
        if not pl.has_share_shortcut(who):
            return print(f"✗ 没找到快捷指令「{pl.shortcut_name(who)}」。先按上面的配方建好。")
        ok, msg = pl.run_shortcut(pl.shortcut_name(who))
        return print("✓ 快捷指令运行成功。去这个人的共享相簿里看,最新一张实况在不在、还会不会动。" if ok else f"✗ 运行失败:{msg}")
    if a.check:
        names = pl.list_shortcuts(0)
        mine = sorted(n[len(pl.SHORTCUT_PREFIX):] for n in names if n.startswith(pl.SHORTCUT_PREFIX))
        print(f"已有快捷指令:{', '.join(pl.SHORTCUT_PREFIX + n for n in mine) or '(没有)'}")
        for who in sorted(set(people) | set(mine)):
            ok, _ = pl.ensure_stage(who, a.flat)
            print(f"  {who}:快捷指令 {'✓' if who in mine else '✗ 还没建'} · 暂存相册 {'✓' if ok else '✗'}")
        return
    if not people:
        ap.error("给 --people 名字,或 --from-server / --check / --test")
    for who in people:
        ok, msg = pl.ensure_stage(who, a.flat)
        print(f"{'✓' if ok else '✗'} 暂存相册「{who}」" + ("" if ok else f":{msg}"))
    print("\n暂存相册建好了。下面是每个人要建的快捷指令(「快捷指令」App → 新建):\n")
    for who in people:
        print(recipe(who, a.flat) + "\n")
    print("建好后运行 python3 share_setup.py --check 确认,再用 --test 名字 试一次,最后给 mac_worker 加 --share 参数。")


if __name__ == "__main__":
    main()
