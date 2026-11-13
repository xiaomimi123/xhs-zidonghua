"""达人库:从千帆「买手广场」采集合作达人的公开信息,按内容分类 / 历史合作品类 / 量级 / 和我们产品的适配度整理,方便筛选后建联;
建联进度(状态、备注、负责人)记在这里。

采集端回传 POST /api/qianfan/ingest {kind:"buyers", items:[...]}。只存列表页能看到的公开字段,
不采集手机号、微信号这类联系方式(要点「发起邀约」才会出现,请在千帆里手动操作)。

字段含义(对应买手广场列表):
- categories   内容分类(达人发什么内容,如 美食、护肤、vlog)
- history_cats 历史合作品类(达人带过哪些品类的货,一级品类名,和筛选里的「合作品类」同一套)
- willing      达人填的意愿合作品类
- coop_tags    平台给的合作标签(积极合作中、邀约回复率高、销售增长快、流量增长快、直播潜力高)"""
import csv
import io
import json
import re
import time
from .db import conn, get_settings, q_all, q_exec, q_one
from .notes import num

STATUSES = {"new": "新发现", "shortlist": "已入围", "contacted": "已联系", "negotiating": "洽谈中", "cooperating": "合作中", "declined": "已拒绝", "ignored": "不合适"}
TIERS = ["头部", "腰部", "初级", "素人"]
BUYER_TAGS = {"1": "积极合作中", "4": "邀约回复率高", "5": "销售增长快", "6": "流量增长快", "7": "直播潜力高"}   # 买手广场 buyer_tag 编码(对照页面标签得出)

ALIASES = {
    "creator_id": ["creator_id", "id", "distributor_id", "user_id", "userId", "达人id", "达人ID", "red_id"],
    "name": ["name", "distributor_name", "nickname", "nickName", "昵称", "达人昵称"],
    "avatar": ["avatar", "avatar_url", "头像"],
    "city": ["city", "location", "城市", "所在地"],
    "level": ["level", "等级"],
    "fans": ["fans", "fans_num", "粉丝数", "粉丝"],
    "categories": ["categories", "content_cats", "content_categorys", "内容分类"],
    "history_cats": ["history_cats", "history_categories", "历史合作品类", "历史合作"],
    "willing": ["willing", "willing_category", "意愿合作品类"],
    "coop_tags": ["coop_tags", "buyer_tag", "tags", "标签"],
    "live_plan": ["live_plan", "直播计划"],
    "gmv": ["gmv", "avg_sale_amount", "场均销售额"],
    "price": ["price", "atv", "客单价"],
    "brand_gmv": ["brand_gmv", "cooperative_brand_agmv", "合作品牌场均销售额"],
    "live_days": ["live_days", "live_day_cnt", "直播天数"],
    "avg_viewers": ["avg_viewers", "avg_live_viewer_num", "场均观看"],
    "note_read_median": ["note_read_median", "note_read_median_num", "笔记阅读中位数"],
    "active_rate": ["active_rate", "active_fans_rate", "活跃粉丝率"],
    "notes_coop": ["notes_coop", "bcoo_order_cnt", "历史笔记合作次数"],
    "red_id": ["red_id", "小红书号"],
    "sex": ["sex", "性别"],
    "url": ["url", "link", "主页链接"],
}


def _pick(d: dict, key: str):
    for a in ALIASES[key]:
        if d.get(a) not in (None, "", []):
            return d[a]
    return None


def _list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = json.loads(v) if v.startswith("[") else re.split(r"[,，、;；|\n]+", v)
    out = []
    for x in v:
        if isinstance(x, dict):
            x = x.get("first_category") or x.get("name") or ""
        s = str(x).strip() if x not in (None, "") else ""
        if s and s not in out:
            out.append(s[:40])
    return out[:20]


def _range(text: str) -> tuple[float, float | None]:
    """「500w以上」→ (5000000, None);「2000-5000」→ (2000, 5000);「50w-100w」→ (500000, 1000000);数字 → (n, n)。"""
    t = str(text or "").replace(" ", "")
    nums = [num(m) for m in re.findall(r"\d[\d.,]*\s*(?:[wWkK万千])?", t)]
    nums = [n for n in nums if n]
    if not nums:
        return 0.0, None
    if "以上" in t or "+" in t or "≥" in t or ">" in t:
        return float(nums[0]), None
    if "以下" in t or "<" in t:
        return 0.0, float(nums[0])
    return float(nums[0]), float(nums[1]) if len(nums) > 1 else float(nums[0])


def tier_of(fans: int) -> str:
    return "头部" if fans >= 1_000_000 else "腰部" if fans >= 100_000 else "初级" if fans >= 10_000 else "素人"


def _csv_setting(key: str) -> list[str]:
    return [t.strip() for t in re.split(r"[,，、\n]+", get_settings().get(key) or "") if t.strip()]


def targets() -> list[str]:
    """我们产品所属的品类关键词(和达人的历史合作品类 / 意愿品类比)。"""
    return _csv_setting("kol_targets")


def content_targets() -> list[str]:
    """和我们产品相关的内容分类关键词(达人发的内容是这类,即使没带过货也算相关)。"""
    return _csv_setting("kol_content")


def collect_cats() -> list[str]:
    """要让采集端去买手广场筛选的合作品类:设置里填了就用,没填用目标品类;都没有 = 不筛选(空串)。"""
    return _csv_setting("kol_cats") or targets() or [""]


def fit_of(content: list[str], hist: list[str], willing: list[str], tg: list[str], ctg: list[str]) -> int:
    """2 = 带过(或愿意带)我们这类品类的货;1 = 只有内容分类相关;0 = 都没有。"""
    hit = lambda xs, ts: any(t in x or x in t for x in xs for t in ts)
    if tg and (hit(hist, tg) or hit(willing, tg)):
        return 2
    return 1 if ctg and hit(content, ctg) else 0


def score_of(item: dict, fit: int, tier: str) -> int:
    """综合评分 0-100:适配度 50 分、合作意愿标签 24 分、有直播计划 6 分、量级 0-10 分、场均销售额 0-8 分、活跃粉丝率 0-8 分。仅用于排序参考。"""
    tags = " ".join(item["coop_tags"])
    s = fit * 25 + (12 if "积极" in tags else 0) + (12 if "回复率高" in tags else 0) + (6 if item["live_plan"] else 0)
    s += {"头部": 6, "腰部": 10, "初级": 8, "素人": 0}[tier] + min(8, int((item["gmv_min"] or 0) / 250000)) + round(min(1.0, item.get("active_rate") or 0) * 8)
    return min(100, s)


def _num_or_zero(v) -> float:
    try:
        if isinstance(v, (int, float)):
            return float(v)
        return float(num(v)) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def normalize(raw: dict) -> dict | None:
    """采集端传来的一行 → 统一字段。没有 ID 也没有名字的丢掉。联系方式类字段一律不取。"""
    cid, name = _pick(raw, "creator_id"), _pick(raw, "name")
    if cid is None and not name:
        return None
    tags = _list(_pick(raw, "coop_tags"))
    gmv, price = str(_pick(raw, "gmv") or ""), str(_pick(raw, "price") or "")
    fans = int(num(_pick(raw, "fans") or 0))
    item = {"creator_id": str(cid or f"name:{name}")[:80], "name": str(name or "")[:60], "avatar": str(_pick(raw, "avatar") or "")[:500], "city": str(_pick(raw, "city") or "")[:30],
            "level": str(_pick(raw, "level") or "")[:10], "fans": fans, "categories": _list(_pick(raw, "categories")), "history_cats": _list(_pick(raw, "history_cats")),
            "willing": _list(_pick(raw, "willing")), "coop_tags": [BUYER_TAGS.get(t, t) for t in tags], "live_plan": str(_pick(raw, "live_plan") or "")[:80],
            "gmv_text": gmv[:30], "price_text": price[:30], "brand_gmv": str(_pick(raw, "brand_gmv") or "")[:30], "live_days": int(_num_or_zero(_pick(raw, "live_days"))),
            "avg_viewers": _num_or_zero(_pick(raw, "avg_viewers")), "note_read_median": int(_num_or_zero(_pick(raw, "note_read_median"))),
            "active_rate": round(_num_or_zero(_pick(raw, "active_rate")), 4), "notes_coop": int(_num_or_zero(_pick(raw, "notes_coop"))),
            "red_id": str(_pick(raw, "red_id") or "")[:30], "sex": str(_pick(raw, "sex") or "")[:4], "url": str(_pick(raw, "url") or "")[:300]}
    item["gmv_min"] = _range(gmv)[0]
    item["price_min"], pmax = _range(price)
    item["price_max"] = pmax if pmax is not None else 0
    item["tier"] = tier_of(fans)
    item["fit"] = fit_of(item["categories"], item["history_cats"], item["willing"], targets(), content_targets())
    item["score"] = score_of(item, item["fit"], item["tier"])
    return item


JSON_FIELDS = ("categories", "history_cats", "willing", "coop_tags")
FIELDS = ("name", "avatar", "city", "level", "fans", "categories", "history_cats", "willing", "coop_tags", "live_plan", "gmv_text", "gmv_min", "price_text", "price_min", "price_max",
          "brand_gmv", "live_days", "avg_viewers", "note_read_median", "active_rate", "notes_coop", "red_id", "sex", "url", "tier", "fit", "score")


def upsert(items: list[dict], source: str = "") -> dict:
    """入库(按 creator_id 去重)。已有的更新采集到的字段,保留我们自己记的状态、备注、负责人。"""
    new = upd = 0
    with conn() as c:
        for raw in items:
            it = normalize(raw) if isinstance(raw, dict) else None
            if not it:
                continue
            vals = [json.dumps(it[f], ensure_ascii=False) if f in JSON_FIELDS else it[f] for f in FIELDS] + [source[:40]]
            row = c.execute("SELECT id FROM kol_creators WHERE creator_id=?", (it["creator_id"],)).fetchone()
            if row:
                c.execute(f"UPDATE kol_creators SET {','.join(f + '=?' for f in FIELDS)},source=?,last_seen=datetime('now'),seen_count=seen_count+1 WHERE id=?", (*vals, row["id"]))
                upd += 1
            else:
                c.execute(f"INSERT INTO kol_creators({','.join(FIELDS)},source,creator_id) VALUES({','.join('?' * (len(FIELDS) + 2))})", (*vals, it["creator_id"]))
                new += 1
    return {"new": new, "updated": upd}


def rescore():
    """目标品类改了以后重新算适配度和评分。"""
    tg, ctg = targets(), content_targets()
    for r in q_all("SELECT id, fans, categories, history_cats, willing, coop_tags, live_plan, gmv_min, active_rate FROM kol_creators"):
        tier = tier_of(r["fans"])
        fit = fit_of(r["categories"], r["history_cats"], r["willing"], tg, ctg)
        q_exec("UPDATE kol_creators SET tier=?, fit=?, score=? WHERE id=?", tier, fit, score_of(r, fit, tier), r["id"])


def query(q: str = "", category: str = "", tier: str = "", status: str = "", fit: int = 0, sort: str = "score", limit: int = 50, offset: int = 0, for_export: bool = False,
          owner: str = "") -> dict:
    """category 同时匹配内容分类和历史合作品类;owner 非空时只要这个人跟进的。for_export=True 时保留明文联系方式(只给导出用)。"""
    rows = q_all("SELECT * FROM kol_creators")
    facets = {"categories": {}, "history": {}, "tiers": {}, "statuses": {}}
    for r in rows:
        for c in r["categories"]:
            facets["categories"][c] = facets["categories"].get(c, 0) + 1
        for c in r["history_cats"]:
            facets["history"][c] = facets["history"].get(c, 0) + 1
        facets["tiers"][r["tier"]] = facets["tiers"].get(r["tier"], 0) + 1
        facets["statuses"][r["status"]] = facets["statuses"].get(r["status"], 0) + 1
    key = q.strip().lower()
    out = [r for r in rows
           if (not key or key in (r["name"] or "").lower() or key in (r["city"] or "").lower() or any(key in c.lower() for c in r["categories"] + r["history_cats"]))
           and (not category or category in r["categories"] or category in r["history_cats"])
           and (not tier or r["tier"] == tier) and (not status or r["status"] == status) and (not fit or r["fit"] >= fit)
           and (not owner or r["owner"] == owner)]
    keyf = {"score": lambda r: (r["score"], r["fans"]), "fans": lambda r: r["fans"], "gmv": lambda r: (r["gmv_min"], r["fans"]), "new": lambda r: r["first_seen"]}.get(sort, lambda r: (r["score"], r["fans"]))
    out.sort(key=keyf, reverse=True)
    top = lambda d, n=40: sorted(({"name": k, "n": v} for k, v in d.items()), key=lambda x: -x["n"])[:n]
    page_rows = out[offset:offset + max(1, min(limit, 200))]
    if not for_export:
        for r in page_rows:   # 列表里不带明文联系方式:只给有没有、手机号打码
            r["has_contact"] = bool(r.get("phone") or r.get("wechat"))
            r["phone_masked"] = mask_phone(r.pop("phone", ""))
            r["has_wechat"] = bool(r.pop("wechat", ""))
    return {"total": len(out), "all": len(rows), "items": page_rows,
            "facets": {"categories": top(facets["categories"]), "history": top(facets["history"]),
                       "tiers": [{"name": t, "n": facets["tiers"].get(t, 0)} for t in TIERS],
                       "statuses": [{"key": k, "name": v, "n": facets["statuses"].get(k, 0)} for k, v in STATUSES.items()]},
            "new_7d": q_one("SELECT COUNT(*) AS n FROM kol_creators WHERE first_seen > datetime('now','-7 days')")["n"]}


def update(cid: int, status: str | None = None, note: str | None = None, owner: str | None = None, by: str = "") -> dict:
    r = q_one("SELECT * FROM kol_creators WHERE id=?", cid)
    if not r:
        raise ValueError("没有这个达人")
    if status is not None:
        if status not in STATUSES:
            raise ValueError("状态不对")
        q_exec("UPDATE kol_creators SET status=?, updated=datetime('now'), owner=CASE WHEN owner='' THEN ? ELSE owner END, "
               "contacted_at=CASE WHEN ? IN ('contacted','negotiating','cooperating') AND contacted_at='' THEN datetime('now') ELSE contacted_at END WHERE id=?", status, by, status, cid)
    if note is not None:
        q_exec("UPDATE kol_creators SET note=?, updated=datetime('now') WHERE id=?", note.strip()[:1000], cid)
    if owner is not None:
        q_exec("UPDATE kol_creators SET owner=?, updated=datetime('now') WHERE id=?", owner.strip()[:30], cid)
    return q_one("SELECT * FROM kol_creators WHERE id=?", cid)


def mask_phone(p: str) -> str:
    return re.sub(r"(\d{3})\d{4}(\d{4})", r"\1****\2", p or "")


def contact_enabled() -> bool:
    return get_settings().get("kol_contact_enabled") == "1"


def contact_used_today() -> int:
    return q_one("SELECT COUNT(*) AS n FROM crawl_tasks WHERE type='qianfan' AND query='buyer_contact' AND date(created,'localtime')=date('now','localtime')")["n"]


def request_contact(cid: int) -> dict:
    """给采集端下发「读取这位达人联系方式」的任务。要管理员打开开关、没超过每日上限,同一位达人不重复下发。"""
    if not contact_enabled():
        raise ValueError("管理员还没有开启「获取联系方式」(管理端 → 接口配置 → 达人采集)")
    r = q_one("SELECT * FROM kol_creators WHERE id=?", cid)
    if not r:
        raise ValueError("没有这个达人")
    if r["contact_status"] == "queued":
        return {"queued": False, "reason": "已经在获取中"}
    if (r["phone"] or r["wechat"]) and r["contact_at"] and q_one("SELECT ? > datetime('now','-30 days') AS ok", r["contact_at"])["ok"]:
        return {"queued": False, "reason": "已经获取过"}
    cap = int(get_settings().get("kol_contact_cap") or 30)
    if contact_used_today() >= cap:
        raise ValueError(f"今天已经获取了 {cap} 位,明天再来(上限可以在管理端调整)")
    params = json.dumps({"creator_id": r["creator_id"], "name": r["name"], "fans": r["fans"], "shop": ""}, ensure_ascii=False)
    from .auth import CURRENT
    q_exec("INSERT INTO crawl_tasks(project_id,type,query,lim,purpose,label,params,created_by) VALUES(NULL,'qianfan','buyer_contact',0,'qianfan',?,?,?)",
           r["name"][:30], params, (CURRENT.get() or {}).get("username", ""))
    q_exec("UPDATE kol_creators SET contact_status='queued', contact_error='' WHERE id=?", cid)
    return {"queued": True}


def pause_contact(reason: str) -> None:
    """触发风控:关掉「获取联系方式」开关,把还没做的任务标失败,记下原因给管理员看。"""
    from .db import save_settings
    save_settings({"kol_contact_enabled": "0", "kol_contact_paused": f"{reason[:150]}({time.strftime('%m-%d %H:%M')})"})
    q_exec("UPDATE crawl_tasks SET status='failed', error='已暂停:触发风控', finished_at=datetime('now') WHERE type='qianfan' AND query='buyer_contact' AND status IN ('pending','running')")
    q_exec("UPDATE kol_creators SET contact_status='error', contact_error='已暂停:触发风控' WHERE contact_status='queued'")


def save_contact(creator_id: str, phone: str = "", wechat: str = "", error: str = "") -> None:
    """采集端回传:读到的手机号 / 微信号(都没有 = 对方没有开放);error = 这次没读成。"""
    phone, wechat = re.sub(r"\s+", "", phone or "")[:20], re.sub(r"\s+", "", wechat or "")[:40]
    if error:
        q_exec("UPDATE kol_creators SET contact_status='error', contact_error=? WHERE creator_id=?", error[:200], creator_id)
    else:
        q_exec("UPDATE kol_creators SET phone=?, wechat=?, contact_status=?, contact_error='', contact_at=datetime('now') WHERE creator_id=?",
               phone, wechat, "ok" if (phone or wechat) else "none", creator_id)


def export_csv(contact: bool = False, **filters) -> str:
    items, off = [], 0
    while True:
        page = query(offset=off, limit=200, for_export=True, **filters)["items"]
        items += page
        if len(page) < 200:
            break
        off += 200
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["昵称", "小红书号", "城市", "粉丝数", "量级", "内容分类", "历史合作品类", "意愿合作品类", "场均销售额", "客单价", "合作品牌场均销售额", "场均观看", "活跃粉丝率", "合作意愿标签",
                "直播计划", "适配度", "评分", "状态", "负责人", "备注", "首次发现", "主页链接"] + (["手机号", "微信号"] if contact else []))
    for r in items:
        w.writerow([r["name"], r["red_id"], r["city"], r["fans"], r["tier"], "、".join(r["categories"]), "、".join(r["history_cats"]), "、".join(r["willing"]), r["gmv_text"], r["price_text"],
                    r["brand_gmv"], r["avg_viewers"] or "", f"{r['active_rate']:.1%}" if r["active_rate"] else "", "、".join(r["coop_tags"]), r["live_plan"],
                    {2: "高", 1: "一般", 0: "低"}[r["fit"]], r["score"], STATUSES[r["status"]], r["owner"], r["note"], r["first_seen"], r["url"]] + ([r["phone"], r["wechat"]] if contact else []))
    return "﻿" + buf.getvalue()   # 带 BOM,Excel 直接打开不乱码
