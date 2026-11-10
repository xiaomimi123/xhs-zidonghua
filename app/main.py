"""入口:用户端 /  ·  管理端 /admin  ·  采集端 /api/tasks、/api/notes"""
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from . import db, auth, worker, notes, api_user, api_admin, api_collector, autorun, api_qianfan, api_live

db.init()
auth.bootstrap()


@asynccontextmanager
async def lifespan(_):
    notes.normalize_existing()  # 给已入库的笔记补做清洗(幂等)
    worker.resume()  # 接着跑上次中断的拆解、复刻,启动打标签线程
    autorun.start()  # 一键批量生成的监督线程
    yield


app = FastAPI(title="小红书流水线工作台", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
STATIC = Path(__file__).resolve().parent.parent / "static"

for r in (api_collector.router, api_admin.public, api_admin.router, api_user.public, api_user.router, api_qianfan.router, api_qianfan.user, api_live.router, api_live.worker_router, api_live.vworker_router):
    app.include_router(r)


@app.middleware("http")
async def no_stale_pages(request, call_next):
    """页面和脚本每次都向服务端确认是否更新,避免升级后浏览器继续用旧的 app.js。"""
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path == "/admin" or request.url.path.endswith((".html", ".js", ".css")):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(STATIC / "admin.html")


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
