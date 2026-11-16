"""登录限流(按账号计数,一个人输错不影响别人;同一 IP 失败太多也锁)+ 数据库定时备份(备份可打开、保留份数、完整性)。"""
import datetime
import sqlite3
import tempfile
from pathlib import Path


class _Req:
    def __init__(self, ip="172.20.0.1"):
        self.client = type("C", (), {"host": ip})()


def run(rep, env):
    from fastapi import HTTPException
    from app import auth, backup

    print("\n[登录限流与备份]")
    auth._fails.clear()

    def locked(who, ip="172.20.0.1"):
        try:
            auth.throttle(_Req(ip), None, who)
            return False
        except HTTPException as e:
            return e.status_code == 429

    for _ in range(5):
        auth.throttle(_Req(), False, "login:a")
    rep.check("同一个账号失败 5 次:这个账号被锁,同一来源地址上的其他账号照常登录", locked("login:a") and not locked("login:b"))
    auth.throttle(_Req(), True, "login:b")
    auth.throttle(_Req("9.9.9.9"), False, "login:c")
    auth.throttle(_Req("9.9.9.9"), True, "login:c")
    rep.check("登录成功会清掉该账号的失败记录", ("who:login:c" not in auth._fails) and not locked("login:c"))
    auth._fails.clear()
    for i in range(30):
        auth.throttle(_Req("8.8.8.8"), False, f"login:u{i}")      # 同一 IP 轮着试不同账号
    rep.check("同一来源地址失败 30 次(轮流试不同账号)也被锁", locked("login:new", "8.8.8.8") and not locked("login:new", "7.7.7.7"))
    auth._fails.clear()

    # ---- 备份 ----
    d = Path(tempfile.mkdtemp())
    o_dir, o_extra, o_keep = backup.DIR, backup.EXTRA, backup.KEEP_DAILY
    backup.DIR, backup.EXTRA, backup.KEEP_DAILY = d / "main", d / "extra", 3
    (d / "extra").mkdir()
    try:
        t0 = datetime.datetime(2026, 1, 1, 3, 0, 0)
        res = [backup.run_once(t0 + datetime.timedelta(days=i)) for i in range(5)]
        names = sorted(p.name for p in (d / "main").glob("workbench-*.db"))
        c = sqlite3.connect(res[-1]["path"])
        try:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            has_staff = "drafts" in tables and "settings" in tables
        finally:
            c.close()
        rep.check("备份:用 SQLite 备份接口生成可打开的库(含 drafts / settings 表),只保留最近 3 份(最新的留下),没有残留的 .part 文件",
                  has_staff and len(names) == 3 and names[-1] == "workbench-20260105-030000.db" and not list((d / "main").glob("*.part")), str(names))
        rep.check("第二个目录(extra)存在时再拷一份,同样只保留最近 3 份", len(list((d / "extra").glob("workbench-*.db"))) == 3 and res[-1]["extra"] != "")
        st = backup.status()
        rep.check("备份状态:有最近一次成功时间、份数、没有错误", st["last_ok"] and st["count"] == 3 and st["last_err"] == "" and st["extra_dir"] is True, str(st))
        backup.EXTRA = d / "没有这个目录"
        r2 = backup.run_once(t0 + datetime.timedelta(days=9))
        rep.check("没挂第二个目录时只做本地备份,不报错", r2["extra"] == "")
    finally:
        backup.DIR, backup.EXTRA, backup.KEEP_DAILY = o_dir, o_extra, o_keep
