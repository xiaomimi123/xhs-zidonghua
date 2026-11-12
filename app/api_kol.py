"""达人库接口:员工查看、筛选、记录建联状态和备注;管理员触发采集、重算适配度、清理。"""
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from . import kol, qianfan
from .auth import CURRENT, require_admin, require_user
from .db import get_settings, q_all, q_exec, q_one

router = APIRouter(prefix="/api/kol", dependencies=[Depends(require_user)])
admin = APIRouter(prefix="/api/admin/kol", dependencies=[Depends(require_admin)])


@router.get("")
def list_kol(q: str = "", category: str = "", tier: str = "", status: str = "", fit: int = 0, sort: str = "score", mine: int = 0,
             limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    me = _me()
    out = kol.query(q, category, tier, status, fit, sort, limit, offset, owner=me if mine and me else "")
    out["me"] = me
    out["mine_n"] = q_one("SELECT COUNT(*) AS n FROM kol_creators WHERE owner=?", me)["n"] if me else 0
    s = get_settings()
    out["targets"] = kol.targets()
    out["content_targets"] = kol.content_targets()
    out["contact_enabled"] = kol.contact_enabled()
    out["contact_paused"] = s.get("kol_contact_paused") or ""
    out["contact_left"] = max(0, int(s.get("kol_contact_cap") or 30) - kol.contact_used_today())
    out["enabled"] = s.get("kol_enabled") == "1"
    last = q_one("SELECT finished_at FROM crawl_tasks WHERE type='qianfan' AND query='buyers' AND status='done' ORDER BY id DESC LIMIT 1")
    out["last_sync"] = last["finished_at"] if last else ""
    out["syncing"] = bool(q_one("SELECT 1 AS x FROM crawl_tasks WHERE type='qianfan' AND query='buyers' AND status IN ('pending','running')"))
    return out


@router.get("/export.csv")
def export_kol(q: str = "", category: str = "", tier: str = "", status: str = "", fit: int = 0, sort: str = "score", contact: int = 0):
    body = kol.export_csv(contact=bool(contact) and kol.contact_enabled(), q=q, category=category, tier=tier, status=status, fit=fit, sort=sort)
    return Response(body, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename*=UTF-8''%E8%BE%BE%E4%BA%BA%E5%BA%93.csv"})


class BatchIn(BaseModel):
    status: str = "shortlist"


@router.post("/contact/batch")
def contact_batch(p: BatchIn):
    """给某个建联状态(默认「已入围」)的、还没有联系方式的达人一次性下发获取任务,受每日上限限制。"""
    if p.status not in kol.STATUSES:
        raise HTTPException(400, "状态不对")
    rows = q_all("SELECT id FROM kol_creators WHERE status=? AND (phone='' AND wechat='') AND contact_status NOT IN ('queued','none') ORDER BY score DESC, fans DESC", p.status)
    n, why = 0, ""
    for r in rows:
        try:
            if kol.request_contact(r["id"])["queued"]:
                n += 1
        except ValueError as e:
            why = str(e)
            break
    return {"queued": n, "candidates": len(rows), "stopped": why}


@router.post("/{cid}/contact")
def contact_request(cid: int):
    try:
        return kol.request_contact(cid)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{cid}/contact")
def contact_get(cid: int):
    """完整的联系方式(列表里只给打码的手机号)。"""
    r = q_one("SELECT name, phone, wechat, contact_at, contact_status, contact_error FROM kol_creators WHERE id=?", cid)
    if not r:
        raise HTTPException(404, "没有这个达人")
    return r


def _me() -> str:
    """认领人记员工的显示名(和管理端、导出 CSV 里看到的一致);管理员记「管理员」。"""
    u = CURRENT.get() or {}
    return (u.get("name") or u.get("username") or "").strip()[:30]


class ClaimIn(BaseModel):
    on: bool = True


@router.post("/{cid}/claim")
def claim(cid: int, p: ClaimIn):
    """认领跟进 / 放手。别人已认领的不能直接抢:请对方放手,或由管理员改负责人。"""
    me = _me()
    r = q_one("SELECT owner FROM kol_creators WHERE id=?", cid)
    if not r:
        raise HTTPException(404, "没有这个达人")
    if not me:
        raise HTTPException(400, "登录后才能认领")
    cur = r["owner"] or ""
    if p.on:
        if cur and cur != me:
            raise HTTPException(409, f"已由「{cur}」跟进,请先和对方确认")
        q_exec("UPDATE kol_creators SET owner=?, updated=datetime('now') WHERE id=?", me, cid)
    else:
        if cur and cur != me:
            raise HTTPException(409, f"这位是「{cur}」在跟进,只有本人或管理员能放手")
        q_exec("UPDATE kol_creators SET owner='', updated=datetime('now') WHERE id=?", cid)
    return {"ok": True, "owner": me if p.on else ""}


class KolPatch(BaseModel):
    status: str | None = None
    note: str | None = None
    owner: str | None = None


@router.post("/{cid}")
def patch_kol(cid: int, p: KolPatch):
    try:
        from .scope import my_id
        owner = p.owner if my_id() is None else None   # 员工改负责人走认领 / 放手,这里只有管理员能直接改
        r = kol.update(cid, p.status, p.note, owner, by=_me())
    except ValueError as e:
        raise HTTPException(400, str(e))
    r.pop("raw", None)
    return r


@admin.post("/sync")
def sync_now():
    """立即给采集端下发「买手广场」采集任务(每个采集品类一个)。"""
    n = 0
    for c in kol.collect_cats():
        if not qianfan._has_open_task("buyers", c, ""):
            qianfan.add_task("buyers", c)
            n += 1
    return {"created": n}


@admin.post("/rescore")
def rescore():
    kol.rescore()
    return {"ok": True}


@admin.delete("/{cid}")
def delete_kol(cid: int):
    q_exec("DELETE FROM kol_creators WHERE id=?", cid)
    return {"ok": True}
