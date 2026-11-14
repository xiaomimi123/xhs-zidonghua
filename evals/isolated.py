"""需要读写数据库、走接口的测试:全部在临时数据库里跑,用最小应用挂载路由,
不碰真实数据库、不碰真实任务队列、不导入 app.main(它会在启动时改动真实的管理员密码文件)。
用例在 cases/iso_*.py,按顺序执行,前面用例造出来的数据通过 env 传给后面的。"""
import tempfile
from pathlib import Path
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from .common import Report

from .cases import iso_collect, iso_notes, iso_autorun, iso_product, iso_library, iso_export, iso_recognize, iso_qianfan, iso_accounts, iso_shops, iso_loop, iso_staff, iso_live, iso_livequeue, iso_learn, iso_titlelab, iso_voice, iso_perf, iso_kol, iso_runs, iso_owner, iso_ptype

CASES = [iso_collect, iso_notes, iso_autorun, iso_product, iso_library, iso_export, iso_recognize, iso_qianfan, iso_accounts, iso_shops, iso_loop, iso_live, iso_livequeue, iso_learn, iso_titlelab, iso_voice, iso_perf, iso_kol, iso_runs, iso_owner, iso_ptype, iso_staff]


def isolated(rep: Report):
    import tempfile
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import db

    real, tmp = db.DB_PATH, Path(tempfile.mkdtemp()) / "isolated.db"
    db.DB_PATH = tmp
    try:
        db.init()
        db.save_settings({"collector_token": "T"})
        from app import api_collector, api_user, api_live, stats
        app = FastAPI()
        for r in (api_collector.router, api_user.public, api_user.router, api_live.router, api_live.worker_router, api_live.vworker_router):
            app.include_router(r)
        user, col = TestClient(app), TestClient(app, headers={"Authorization": "Bearer T"})
        q = lambda sql, *a: db.q_all(sql, *a)
        env = SimpleNamespace(app=app, col=col, q=q, r=r, stats=stats, user=user)
        for case in CASES:
            case.run(rep, env)
    finally:
        db.DB_PATH = real
