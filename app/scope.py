"""数据归属:员工只能看到、改动自己的任务(及其下面的采集任务、拆解卡、稿子)、Live 图任务、导出包和生成的图片。
管理员、没开登录时(没有员工账号)、后台线程不受限制。

归属只记在两个地方:projects.owner_id(稿子、拆解卡、采集任务都经 project_id 找归属)和 live_jobs.owner_id。
NULL = 管理员 / 历史数据,员工看不到。

带 id 的接口不用各自校验:guard() 挂在 auth.require_user 里,按路由路径参数名统一校验。
新加带路径参数的接口时,参数名要么在 PARAM_KIND 里,要么登记到 SHARED_PARAMS(evals 会检查,漏了就失败)。"""
import re
from fastapi import HTTPException, Request
from .auth import CURRENT
from .db import q_all, q_one

# 路径参数名 → 资源类型
PARAM_KIND = {"pid": "project", "did": "draft", "bid": "playbook", "tid": "crawl", "jid": "live"}

# 不按人隔离的路径参数:(路由路径, 参数名) → 原因
SHARED_PARAMS = {
    ("/api/products/{pid}", "pid"): "产品全员共享",
    ("/product_images/{name}", "name"): "产品图全员共享",
    ("/note_images/{name}", "name"): "笔记库图片全员共享",
    ("/api/projects/{pid}/notes/{nid}", "nid"): "笔记库全员共享(pid 已校验)",
    ("/api/kol/{cid}", "cid"): "达人库全员共享",
    ("/api/kol/{cid}/contact", "cid"): "达人库全员共享",
    ("/api/kol/{cid}/claim", "cid"): "达人库全员共享(认领规则在接口里)",
    ("/images/{name}", "name"): "生成图:guard 里按文件名单独校验",
    ("/download/{name}", "name"): "导出包:guard 里按导出记录单独校验",
    ("/api/export/{job_id}", "job_id"): "导出进度:接口里按发起人校验",
}

_OWNER_SQL = {
    "project": "SELECT owner_id FROM projects WHERE id=?",
    "draft": "SELECT p.owner_id FROM drafts d JOIN projects p ON p.id=d.project_id WHERE d.id=?",
    "playbook": "SELECT p.owner_id FROM playbooks b JOIN projects p ON p.id=b.project_id WHERE b.id=?",
    "crawl": "SELECT p.owner_id FROM crawl_tasks t JOIN projects p ON p.id=t.project_id WHERE t.id=?",
    "live": "SELECT owner_id FROM live_jobs WHERE id=?",
}


def my_id():
    """当前员工 id;管理员 / 没开登录 / 后台线程返回 None。"""
    u = CURRENT.get()
    return u.get("id") if u else None


def sees_all() -> bool:
    return my_id() is None


def owner_sql(alias: str = "p") -> tuple[str, list]:
    """拼到列表查询 WHERE 后面:(" AND p.owner_id=?", [id]);能看全部时为 ("", [])。"""
    sid = my_id()
    return ("", []) if sid is None else (f" AND {alias}.owner_id=?", [sid])


def _deny():
    # 不存在和不是自己的一样回 404,不泄露这个 id 是否存在
    raise HTTPException(404, "不存在")


def check(kind: str, rid) -> None:
    sid = my_id()
    if sid is None:
        return
    try:
        rid = int(rid)
    except (TypeError, ValueError):
        _deny()
    r = q_one(_OWNER_SQL[kind], rid)
    if not r or r["owner_id"] != sid:
        _deny()


def visible(kind: str, ids: list[int]) -> list[int]:
    """批量操作时滤掉不是自己的 id(保持原顺序)。"""
    sid = my_id()
    if sid is None or not ids:
        return list(ids)
    mine = set()
    for i in set(ids):
        r = q_one(_OWNER_SQL[kind], i)
        if r and r["owner_id"] == sid:
            mine.add(i)
    return [i for i in ids if i in mine]


_DRAFT_IMG = re.compile(r"^d(\d+)_")


def check_image(name: str) -> None:
    """生成图 d{稿子id}_…:按稿子归属;Live 图上传 / 生成的图:要有自己的 Live 任务用到它。"""
    sid = my_id()
    if sid is None:
        return
    m = _DRAFT_IMG.match(name)
    if m:
        return check("draft", m.group(1))
    if q_one("SELECT 1 AS x FROM live_jobs WHERE owner_id=? AND (image=? OR gen LIKE ?) LIMIT 1", sid, name, f"%{name}%"):
        return
    _deny()


def check_export(name: str) -> None:
    sid = my_id()
    if sid is None:
        return
    r = q_one("SELECT owner_id FROM exports WHERE zip=?", name)
    if not r or r["owner_id"] != sid:
        _deny()


def guard(request: Request) -> None:
    """由 auth.require_user 在确定登录人之后调用。"""
    if sees_all():
        return
    route = request.scope.get("route")
    path = getattr(route, "path", "")
    for k, v in request.path_params.items():
        if (path, k) in SHARED_PARAMS:
            continue
        kind = PARAM_KIND.get(k)
        if kind:
            check(kind, v)
    if path == "/images/{name}":
        check_image(request.path_params.get("name", ""))
    elif path == "/download/{name}":
        check_export(request.path_params.get("name", ""))


def staff_names() -> dict:
    return {r["id"]: r["name"] or r["username"] for r in q_all("SELECT id, name, username FROM staff")}
