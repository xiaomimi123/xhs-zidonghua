"""数据库定时备份:每天一次(用 SQLite 备份接口,WAL 库不能直接拷文件),存在 data/backups/,
如果挂了第二个目录(compose 里的 BACKUP_EXTRA_DIR,建议放在另一块盘或另一台机器的共享目录)就再拷一份。
只保留最近 KEEP_DAILY 份;启动时如果最近一份已经超过一天,立刻补做一次。"""
import datetime
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from .db import DATA, DB_PATH

DIR = DATA / "backups"
EXTRA = Path("/app/backups")          # compose 把 BACKUP_EXTRA_DIR 挂到这里
KEEP_DAILY = 14
INTERVAL_H = 24
_state = {"last_ok": "", "last_err": "", "running": False}
_lock = threading.Lock()


def _prune(d: Path):
    files = sorted(d.glob("workbench-*.db"))
    for f in files[:-KEEP_DAILY]:
        f.unlink(missing_ok=True)


def run_once(now: datetime.datetime | None = None) -> dict:
    """做一次备份,返回 {path, extra, size}。同时只跑一个;任何一步失败都会记到 last_err 再抛出。"""
    with _lock:
        _state["running"] = True
        try:
            DIR.mkdir(parents=True, exist_ok=True)
            name = f"workbench-{(now or datetime.datetime.now()):%Y%m%d-%H%M%S}.db"
            tmp, dst = DIR / (name + ".part"), DIR / name
            src, out = sqlite3.connect(DB_PATH, timeout=30), sqlite3.connect(tmp)
            try:
                src.backup(out)
            finally:
                out.close(); src.close()
            chk = sqlite3.connect(tmp)
            try:
                ok = chk.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            finally:
                chk.close()
            if not ok:
                tmp.unlink(missing_ok=True)
                raise RuntimeError("备份文件没通过完整性检查")
            tmp.replace(dst)
            _prune(DIR)
            extra = ""
            if EXTRA.is_dir():
                shutil.copy2(dst, EXTRA / name)
                _prune(EXTRA)
                extra = str(EXTRA / name)
            _state.update(last_ok=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), last_err="")
            return {"path": str(dst), "extra": extra, "size": dst.stat().st_size}
        except Exception as e:
            _state["last_err"] = f"{type(e).__name__}: {e}"[:300]
            raise
        finally:
            _state["running"] = False


def latest() -> Path | None:
    fs = sorted(DIR.glob("workbench-*.db")) if DIR.is_dir() else []
    return fs[-1] if fs else None


def status() -> dict:
    l = latest()
    return {"last_ok": _state["last_ok"] or (datetime.datetime.fromtimestamp(l.stat().st_mtime).strftime("%Y-%m-%d %H:%M") if l else ""),
            "last_err": _state["last_err"], "count": len(list(DIR.glob("workbench-*.db"))) if DIR.is_dir() else 0,
            "extra_dir": EXTRA.is_dir(), "keep": KEEP_DAILY}


def _loop():
    time.sleep(60)   # 让启动先稳定下来
    while True:
        l = latest()
        if not l or time.time() - l.stat().st_mtime > INTERVAL_H * 3600 - 600:
            try:
                run_once()
            except Exception as e:
                print(f"[备份] 失败:{e}", flush=True)
        time.sleep(1800)


def start():
    threading.Thread(target=_loop, daemon=True, name="backup").start()
