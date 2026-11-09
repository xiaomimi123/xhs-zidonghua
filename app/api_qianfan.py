"""千帆数据看板接口:管理端看全部,员工端只看热门话题 / 关键词和自己稿子的表现。"""
import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from . import qianfan
from .auth import require_admin, require_user
from .db import get_settings, q_all, q_exec, q_one, save_settings

router = APIRouter(prefix="/api/admin/qianfan", dependencies=[Depends(require_admin)])
user = APIRouter(prefix="/api/qianfan", dependencies=[Depends(require_user)])

SETTING_KEYS = ("qf_enabled", "qf_kinds", "qf_account_hours", "qf_hot_hours", "qf_categories", "qf_periods")


@router.get("/overview")
def overview(account: str = "", shop: str = ""):
    """shop 为空看全部店铺;account 为空看全部账号;传了就只看这个店铺 / 账号的趋势和笔记表现。"""
    s = get_settings()
    shop_list = qianfan.shops()
    sid = shop if shop in {x["shop_id"] for x in shop_list} else ""
    accs = qianfan.accounts(sid or None)
    ids = {a["account_id"] for a in accs}
    sel = account if account in ids else ""
    acc = qianfan.series(90, sel) if sel else qianfan.series(90, accs[0]["account_id"]) if len(accs) == 1 else []
    cur = next((a for a in accs if a["account_id"] == sel), None) if sel else (accs[0] if len(accs) == 1 else None)
    perf_all, at = qianfan.notes_all(sid)
    by_acc: dict[str, int] = {}
    for it in perf_all:
        by_acc[it.get("account_id", "")] = by_acc.get(it.get("account_id", ""), 0) + 1
    perf = [it for it in perf_all if it.get("account_id", "") == sel] if sel else perf_all
    matched = qianfan.match_drafts(perf)
    for it in perf:
        it["mine"] = any(v is it for v in matched.values())
    top = sorted(perf, key=lambda r: r.get("engagement", 0), reverse=True)
    return {"settings": {k: s[k] for k in SETTING_KEYS}, "status": qianfan.status(), "sample": qianfan.has_sample(),
            "shops": shop_list, "shop": sid,
            "accounts": accs, "selected": sel, "note_counts": by_acc,
            "account": cur, "series": acc,
            "notes": {"at": at, "items": top[:200], "matched": len(matched), "total": len(perf)},
            "scopes": {k: (qianfan.scopes_any(k) if k in qianfan.SHOP_FREE else qianfan.scopes(k, sid)) for k in qianfan.HOT}}


@router.get("/hot")
def hot(kind: str, scope: str = "", period: str = "", shop: str = ""):
    if kind not in qianfan.HOT:
        raise HTTPException(400, "kind 不对")
    snap = qianfan.latest_any(kind, scope, period) if kind in qianfan.SHOP_FREE else qianfan.latest(kind, scope, period, shop)
    return {"at": snap["snap_at"] if snap else "", "items": (snap["data"] or {}).get("items", []) if snap else []}


@router.get("/topics")
def topics(account: str = "", shop: str = ""):
    """选题分析:按账号汇总哪类内容、哪些主题表现好。"""
    return qianfan.topic_report(account, shop)


@router.post("/topics/tag")
def topics_tag():
    """立即给还没分类的笔记归类(后台跑,前端轮询 /topics 看进度)。"""
    st = qianfan.start_tagging()
    if st.get("error") and not st.get("running"):
        raise HTTPException(400, st["error"])
    return st


class SettingsIn(BaseModel):
    qf_enabled: str = "0"
    qf_kinds: str = ""
    qf_account_hours: str = "24"
    qf_hot_hours: str = "12"
    qf_categories: str = ""
    qf_periods: str = "7d"


@router.post("/settings")
def save(p: SettingsIn):
    d = p.model_dump()
    for k in ("qf_account_hours", "qf_hot_hours"):
        try:
            d[k] = str(max(1, min(int(float(d[k])), 24 * 30)))
        except ValueError:
            d[k] = "24"
    save_settings(d)
    return {"ok": True}


class SyncIn(BaseModel):
    kinds: list[str] = []


@router.post("/sync")
def sync(p: SyncIn):
    """立即同步:不看间隔,没有未完成同步任务的类别马上新建任务,等采集端来领。"""
    n = qianfan.schedule(force=True, kinds=[k for k in p.kinds if k in qianfan.KINDS] or None)
    return {"ok": True, "created": n}


@router.post("/import")
def import_json(body: dict):
    """没接上采集端之前,可以直接把千帆页面的数据整理成 JSON 粘贴进来(格式同 /api/qianfan/ingest)。"""
    try:
        return qianfan.ingest(body)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/sample")
def sample():
    return {"ok": True, "stored": qianfan.load_sample()}


@router.delete("/sample")
def sample_clear():
    return {"ok": True, "removed": qianfan.clear_sample()}


class TitleTasksIn(BaseModel):
    titles: list[str]


@router.post("/to_title_tasks")
def to_title_tasks(p: TitleTasksIn):
    """热门笔记标题 → 笔记库沉淀任务(按标题去搜,抓到原笔记进库)。"""
    n = 0
    for t in dict.fromkeys(x.strip() for x in p.titles if x and x.strip()):
        if q_one("SELECT 1 FROM crawl_tasks WHERE type='title' AND purpose='library' AND query=? AND status IN ('pending','running')", t):
            continue
        q_exec("INSERT INTO crawl_tasks(project_id,type,query,lim,purpose,label) VALUES(NULL,'title',?,1,'library','千帆热门')", t)
        n += 1
    return {"ok": True, "created": n}


@user.get("/hot")
def user_hot(track: str = ""):
    return qianfan.hot_names(track)


def _staff_board_guard(o: dict) -> dict:
    o.pop("settings", None)   # 同步设置是管理员的
    return o


@user.get("/board/overview")
def board_overview(account: str = "", shop: str = ""):
    """员工端数据看板(只读):和管理端同一份数据,去掉同步设置。"""
    return _staff_board_guard(overview(account, shop))


@user.get("/board/hot")
def board_hot(kind: str, scope: str = "", period: str = "", shop: str = ""):
    return hot(kind, scope, period, shop)


@user.get("/board/topics")
def board_topics(account: str = "", shop: str = ""):
    return topics(account, shop)


@user.get("/accounts")
def user_accounts():
    """员工端选「发布到哪个账号」用:店铺下的账号(来自千帆同步)。"""
    return [{k: a.get(k) for k in ("account_id", "name", "red_id", "fans", "shop_id", "shop_name")} for a in qianfan.accounts() if a["account_id"]]


@user.get("/account_plan")
def user_account_plan(account_id: str):
    """新建任务选了发布账号后:按这个账号的数据推荐该写的选题角度(带依据)。"""
    if qianfan.account_name(account_id) is None:
        raise HTTPException(404, "找不到这个账号")
    return qianfan.recommend(account_id)


@user.get("/account_keywords")
def user_account_keywords(account_id: str):
    """按账号的内容画像推荐采集用的搜索词(调 AI,几秒钟;AI 不可用时返回空)。"""
    if qianfan.account_name(account_id) is None:
        raise HTTPException(404, "找不到这个账号")
    try:
        return {"keywords": qianfan.account_keywords(account_id)}
    except Exception as e:
        return {"keywords": [], "error": str(e)[:120]}


@user.get("/performance")
def user_performance():
    """员工端简版:账号最新数据 + 我们发布的稿子的表现(按标题对上号,带上发布到了哪个账号)。"""
    accs = qianfan.accounts()
    main = accs[0] if accs else None   # 粉丝最多的当主账号
    items, notes_at = qianfan.notes_all()
    m = qianfan.match_drafts(items)
    mine = [{"draft_id": did, **{k: it.get(k) for k in ("title", "views", "likes", "collects", "comments", "new_fans", "account_name")}} for did, it in m.items()]
    mine.sort(key=lambda r: (r.get("likes") or 0) + (r.get("collects") or 0), reverse=True)
    return {"account": main, "prev": main.get("prev") if main else None, "accounts": accs,
            "at": notes_at, "mine": mine[:20]}
