"""Mac「照片」App 的整理:相册结构 = 按人建文件夹,里面按天分相册。

    照片 App 侧边栏
    ├─ 张怡              ← 文件夹(一个人一个)
    │   ├─ 2026-11-14   ← 相册(一天一个)
    │   └─ 2026-11-13
    ├─ 润娜
    └─ 未署名            ← 工作台没给发起人的任务

导入时用「import … into album」一步到位(不再按文件名去猜刚导入的是哪一张);这条命令不行时退回「先导入、再按文件名归类」。
只能在 macOS 上用(osascript 控制「照片」)。
"""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

NO_OWNER = "未署名"
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _q(s: str) -> str:
    """AppleScript 字符串里的转义。"""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def osa(script: str, timeout: int = 60) -> tuple[bool, str]:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    return r.returncode == 0, (r.stdout if r.returncode == 0 else (r.stderr or r.stdout)).strip()


def clean_person(by: str) -> str:
    """文件夹名:去掉会让 AppleScript 出问题的字符;空 = 未署名。"""
    return re.sub(r'["\\\n\r]', "", (by or "").strip())[:40] or NO_OWNER


def clean_day(day: str) -> str:
    d = (day or "").strip()[:10]
    return d if DAY_RE.match(d) else time.strftime("%Y-%m-%d")


def ensure_album(person: str, day: str) -> tuple[bool, str]:
    """保证「person」文件夹和里面名为「day」的相册都存在。"""
    p, d = _q(clean_person(person)), _q(clean_day(day))
    return osa(f'''
    tell application "Photos"
        if not (exists folder "{p}") then make new folder named "{p}"
        set f to folder "{p}"
        if not (exists album "{d}" of f) then make new album named "{d}" at f
        return "ok"
    end tell''')


def import_pvt(pvt: Path, person: str, day: str, timeout: int = 600) -> tuple[bool, str]:
    """把一个 .pvt 导入照片 App,并放进「person」文件夹里的「day」相册。返回 (是否成功, 说明)。
    1) 先建好文件夹 / 相册,再 import … into album;2) 这条命令不行,就普通导入后按文件名把它加进相册。"""
    ok, msg = ensure_album(person, day)
    if not ok:
        return False, f"建文件夹 / 相册失败:{msg[:150]}"
    p, d, path = _q(clean_person(person)), _q(clean_day(day)), _q(pvt.resolve())
    ok, msg = osa(f'''
    tell application "Photos"
        set al to album "{d}" of folder "{p}"
        import POSIX file "{path}" into al
        return "ok"
    end tell''', timeout=timeout)
    if ok:
        return True, "ok"
    # 退路:普通导入 + 按文件名归类
    ok2, msg2 = osa(f'tell application "Photos" to import POSIX file "{path}"', timeout=timeout)
    if not ok2:
        return False, f"导入失败:{msg2[:150]}"
    time.sleep(5)
    stem = _q(pvt.name.replace(".pvt", ""))
    ok3, msg3 = osa(f'''
    tell application "Photos"
        set matches to every media item whose filename starts with "{stem}"
        if (count of matches) is 0 then return "no-match"
        set al to album "{d}" of folder "{p}"
        add {{last item of matches}} to al
        return "ok"
    end tell''')
    if ok3 and msg3 == "ok":
        return True, "ok(退路:按文件名归类)"
    return False, f"已导入但没能归入相册(图在「最近项目」里):{(msg3 or '未知原因')[:120]}"


def album_count(person: str, day: str) -> int:
    """这个人这一天的相册里现在有几张(查不到返回 -1)。"""
    ok, out = osa(f'''
    tell application "Photos"
        return count of media items of (album "{_q(clean_day(day))}" of folder "{_q(clean_person(person))}")
    end tell''')
    return int(out) if ok and out.isdigit() else -1


# ---------------- 自动加入共享相簿 ----------------
# 「照片」的脚本接口不能操作共享相簿,所以绕一步:导入成功后,把这张图再放进「待分享」文件夹里这个人的暂存相册,
# 然后运行这个人对应的快捷指令「加入共享-张怡」(动作:查找照片 [相册 = 待分享/张怡,按添加日期倒序,数量 1] → 添加到共享相簿 [张怡])。
# 暂存相册里只有我们放进去的图,iPhone 同步来的照片不会混进来,所以「最新一张」一定是刚导入的这张。
STAGE_FOLDER = "待分享"
SHORTCUT_PREFIX = "加入共享-"
_sc_cache: dict = {"at": 0.0, "names": set()}


def shortcut_name(person: str) -> str:
    return SHORTCUT_PREFIX + clean_person(person)


def list_shortcuts(max_age: float = 60.0) -> set[str]:
    """本机「快捷指令」里有哪些快捷指令(缓存 1 分钟:新建了快捷指令不用重启 worker)。"""
    if time.time() - _sc_cache["at"] > max_age:
        r = subprocess.run(["shortcuts", "list"], capture_output=True, text=True, timeout=60)
        _sc_cache["names"] = {x.strip() for x in r.stdout.splitlines() if x.strip()} if r.returncode == 0 else set()
        _sc_cache["at"] = time.time()
    return _sc_cache["names"]


def has_share_shortcut(person: str) -> bool:
    return shortcut_name(person) in list_shortcuts()


def ensure_stage(person: str, flat: bool = False) -> tuple[bool, str]:
    """保证这个人的暂存相册存在:「待分享」文件夹里一个叫她名字的相册(flat=True 时是顶层相册「待分享-张怡」,快捷指令里选不到文件夹里的相册时用)。"""
    p = _q(clean_person(person))
    if flat:
        return osa(f'''
        tell application "Photos"
            if not (exists album "{STAGE_FOLDER}-{p}") then make new album named "{STAGE_FOLDER}-{p}"
            return "ok"
        end tell''')
    return osa(f'''
    tell application "Photos"
        if not (exists folder "{STAGE_FOLDER}") then make new folder named "{STAGE_FOLDER}"
        set f to folder "{STAGE_FOLDER}"
        if not (exists album "{p}" of f) then make new album named "{p}" at f
        return "ok"
    end tell''')


def stage_latest(pvt: Path, person: str, flat: bool = False) -> tuple[bool, str]:
    """把刚导入的这张(按文件名找最新一张)放进这个人的暂存相册。"""
    ok, msg = ensure_stage(person, flat)
    if not ok:
        return False, f"建暂存相册失败:{msg[:120]}"
    p, stem = _q(clean_person(person)), _q(pvt.name.replace(".pvt", ""))
    target = f'album "{STAGE_FOLDER}-{p}"' if flat else f'album "{p}" of folder "{STAGE_FOLDER}"'
    ok, msg = osa(f'''
    tell application "Photos"
        set matches to every media item whose filename starts with "{stem}"
        if (count of matches) is 0 then return "no-match"
        add {{last item of matches}} to {target}
        return "ok"
    end tell''')
    return (True, "ok") if ok and msg == "ok" else (False, f"没能放进暂存相册:{(msg or '未知原因')[:120]}")


def run_shortcut(name: str, timeout: int = 180) -> tuple[bool, str]:
    r = subprocess.run(["shortcuts", "run", name], capture_output=True, text=True, timeout=timeout)
    return r.returncode == 0, (r.stderr or r.stdout).strip()[:150]


def share_after_import(pvt: Path, person: str, flat: bool = False) -> tuple[bool, str]:
    """导入成功后:放进暂存相册 → 运行这个人的「加入共享-xxx」快捷指令(失败重试一次)。
    返回 (成功, 说明)。没给发起人(未署名)、没建对应快捷指令的不共享,说明里写清原因。"""
    who = clean_person(person)
    if who == NO_OWNER:
        return False, "没有发起人(未署名),不共享"
    if not has_share_shortcut(who):
        return False, f"没有快捷指令「{shortcut_name(who)}」,没共享(图在「{who}」文件夹里)"
    ok, msg = stage_latest(pvt, who, flat)
    if not ok:
        return False, msg
    for attempt in (1, 2):
        try:
            ok, msg = run_shortcut(shortcut_name(who))
        except subprocess.TimeoutExpired:
            ok, msg = False, "快捷指令运行超时"
        if ok:
            return True, f"已加入「{who}」的共享相簿"
        if attempt == 1:
            time.sleep(5)
    return False, f"快捷指令运行失败:{msg}(图已在暂存相册里,可以手动加进共享相簿)"


# ---------------- 整理已有的相册 ----------------
OLD_RE = re.compile(r"^(.+?)\s*·\s*(\d{4}-\d{2}-\d{2})$")   # 旧的「张怡 · 2026-11-14」平铺相册


def list_top_albums() -> list[dict]:
    """顶层(不在文件夹里)的所有相册:名字和张数。"""
    ok, out = osa('''
    tell application "Photos"
        set res to ""
        repeat with a in albums
            set res to res & (name of a) & tab & (count of media items of a) & linefeed
        end repeat
        return res
    end tell''', timeout=300)
    if not ok:
        raise RuntimeError(out[:200])
    rows = []
    for line in out.splitlines():
        if "\t" in line:
            n, c = line.rsplit("\t", 1)
            rows.append({"name": n, "count": int(c) if c.strip().isdigit() else 0})
    return rows


def move_old_album(name: str, person: str, day: str) -> tuple[bool, str]:
    """把旧的平铺相册里的照片加进新结构的相册,然后删掉旧相册(只删相册,照片本身不动)。空相册直接删。"""
    n = _q(name)
    ok, msg = osa(f'tell application "Photos" to return count of media items of album "{n}"')
    if not ok:
        return False, msg[:120]
    has = msg.isdigit() and int(msg) > 0
    if has:
        ok, msg = ensure_album(person, day)
        if not ok:
            return False, msg[:120]
        p, d = _q(clean_person(person)), _q(clean_day(day))
        ok, msg = osa(f'''
        tell application "Photos"
            set items_ to media items of album "{n}"
            add items_ to (album "{d}" of folder "{p}")
            return "ok"
        end tell''', timeout=600)
        if not ok:
            return False, msg[:120]
    ok, msg = osa(f'tell application "Photos" to delete album "{n}"')
    return ok, "ok" if ok else msg[:120]


def selftest() -> None:
    """不碰真实数据:建一个临时文件夹和相册,确认脚本命令都能用,然后删掉。"""
    import sys
    steps = [
        ("建文件夹和相册", lambda: ensure_album("_测试文件夹", "2000-01-01")),
        ("相册能查到(0 张)", lambda: (album_count("_测试文件夹", "2000-01-01") == 0, str(album_count("_测试文件夹", "2000-01-01")))),
        ("删相册", lambda: osa('tell application "Photos" to delete album "2000-01-01" of folder "_测试文件夹"')),
        ("删文件夹", lambda: osa('tell application "Photos" to delete folder "_测试文件夹"')),
    ]
    bad = 0
    for name, fn in steps:
        try:
            ok, msg = fn()
        except Exception as e:
            ok, msg = False, str(e)
        print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f":{msg[:160]}"))
        bad += not ok
    print("全部通过,照片 App 的文件夹 / 相册命令可用。" if not bad else f"有 {bad} 项没通过:把上面的 ✗ 那几行发给我。")
    sys.exit(1 if bad else 0)
