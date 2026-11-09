"""数据库版本与迁移:新库、老库升级、迁移只跑一次、迁移前备份、拒绝用旧程序打开新库。用自己的临时数据库。"""
import sqlite3
import tempfile
from pathlib import Path


def run(rep):
    from app import db

    print("\n[数据库版本与迁移]")
    real, orig = db.DB_PATH, list(db.MIGRATIONS)
    tmp = Path(tempfile.mkdtemp())
    try:
        db.DB_PATH = tmp / "fresh.db"
        db.init()
        with db.conn() as c:
            vers = [r[0] for r in c.execute("SELECT version FROM schema_version ORDER BY version")]
        rep.check("新库:建好全部结构并记为最新版本", vers == [m[0] for m in db.MIGRATIONS], str(vers))
        rep.check("新库(空库)迁移前不产生备份", not list(tmp.glob("*.bak.db")), str(list(tmp.iterdir())))
        db.init()
        with db.conn() as c:
            n = c.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
        rep.check("重复启动不重复执行迁移", n == len(db.MIGRATIONS), str(n))

        # 老库:有数据、没有 schema_version 表 → 升级后补记版本,并先备份,数据原样保留
        db.DB_PATH = tmp / "old.db"
        db.init()
        db.q_exec("INSERT INTO projects(track) VALUES('老数据')")
        with db.conn() as c:
            c.execute("DROP TABLE schema_version")
        db.init()
        with db.conn() as c:
            n = c.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
        rep.check("老库升级:补记全部版本,数据不丢", n == len(db.MIGRATIONS) and db.q_one("SELECT track FROM projects")["track"] == "老数据")
        rep.check("老库升级前自动备份", (tmp / "old.pre-migrate-v1.bak.db").is_file())

        # 新增一版迁移:只对没跑过的库执行一次,先备份,新字段出现
        def m2(c):
            db.add_column(c, "projects", "eval_col", "TEXT DEFAULT ''")
        nxt = db.MIGRATIONS[-1][0] + 1
        db.MIGRATIONS.append((nxt, "eval", m2))
        db.init()
        db.init()
        with db.conn() as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(projects)")}
            n = c.execute("SELECT COUNT(*) FROM schema_version WHERE name='eval'").fetchone()[0]
        rep.check("新迁移:字段加上了,且只记一次", "eval_col" in cols and n == 1, f"{'eval_col' in cols} {n}")
        bak = tmp / f"old.pre-migrate-v{nxt}.bak.db"
        rep.check("新迁移前自动备份,备份里还没有新字段", bak.is_file() and "eval_col" not in {r[1] for r in sqlite3.connect(bak).execute("PRAGMA table_info(projects)")})

        # 库比程序新:拒绝启动,不改库
        db.MIGRATIONS[:] = orig
        try:
            db.init()
            refused = False
        except RuntimeError as e:
            refused = "更新" in str(e)
        rep.check("数据库版本比程序新:拒绝启动", refused)
    finally:
        db.MIGRATIONS[:] = orig
        db.DB_PATH = real
