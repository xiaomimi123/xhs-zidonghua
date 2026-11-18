"""稿子的发布状态:员工核对后标成「被平台删除 / 没发出去」(不再算没对上、不进复盘统计、被删的记原因当违规样本);
「标了发布但没对上」按登记天数从久到近排,超期的提示核对;复盘页「已处理」一栏;千帆里其实找得到 / 重新登记时状态作废。"""
import datetime
import json
import time


def run(rep, env):
    from app import api_user, db, loop, qianfan

    print("\n[发布状态:被删 / 没发出去]")
    u = env.user
    day = lambda n: (datetime.date.today() - datetime.timedelta(days=n)).isoformat()
    # ---- 迁移:列和覆盖索引 ----
    cols = {r["name"] for r in db.q_all("PRAGMA table_info(drafts)")}
    idx = [r["name"] for r in db.q_all("PRAGMA index_info(ix_drafts_loop)")]
    plan = " ".join(str(r["detail"]) for r in db.q_all(
        "EXPLAIN QUERY PLAN SELECT d.id, d.title, d.angle, d.account_id, d.account_name, d.project_id, d.published, d.published_at, d.qf_note_id, d.status, d.auto_ok, d.created, d.images, d.cover_card, d.ptype, d.feat, d.pub_edit, d.pub_state FROM drafts d WHERE d.title!=''"))
    rep.check("迁移:稿子表有发布状态的三列;复盘用的覆盖索引带上了 pub_state(评估查询仍只读索引,不读正文)",
              {"pub_state", "pub_state_note", "pub_state_at"} <= cols and "pub_state" in idx and "COVERING INDEX ix_drafts_loop" in plan, plan[:120])

    pj = db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('美食','[]','')").lastrowid
    mk = lambda title, pub=0, pday="", extra=None: db.q_exec(
        "INSERT INTO drafts(project_id,title,content,status,published,published_at,ptype,checks) VALUES(?,?,?,'ready',?,?,'产品软文',?)", pj, title, "正文", pub, pday,
        json.dumps(extra or {}, ensure_ascii=False)).lastrowid
    a_old = mk("状态测试甲:登记了五天", 1, day(5))
    a_new = mk("状态测试乙:今天才登记", 1, day(0))
    a_mid = mk("状态测试丙:登记三天", 1, day(3), {"hits": [{"word": "最好", "category": "广告法极限词"}, {"word": "亲测", "category": "买家口吻(平台判违规)"}]})
    a_todo = mk("状态测试丁:还没登记")
    rows = lambda: {r["id"]: r for r in loop.evaluate(register=False)["rows"]}
    r0 = rows()
    ov0 = loop.overview(loop.evaluate(register=False))
    rep.check("没处理前:标了发布但千帆里没有 = 没绑定笔记(nodata),没标发布 = 未发布;超 2 天没对上的有 2 篇(登记 5 天、3 天的)",
              r0[a_old]["tier"] == "nodata" and r0[a_new]["tier"] == "nodata" and r0[a_todo]["tier"] == "unpublished" and ov0["stale_nodata"] >= 2, str((r0[a_old]["tier"], ov0["stale_nodata"])))

    # ---- 标记 ----
    bad = u.post("/api/drafts/state", json={"ids": [a_old], "state": "乱写"})
    empty = u.post("/api/drafts/state", json={"ids": [], "state": "removed"})
    rep.check("状态接口:状态不合法 / 没选稿子都拒绝(400)", bad.status_code == 400 and empty.status_code == 400)
    n_pub = loop.overview(loop.evaluate(register=False))["funnel"]
    pub_before = next(x["n"] for x in n_pub if x["key"] == "published")
    todo_before = loop.overview(loop.evaluate(register=False))["bind_todo"]["total"]
    u.post("/api/drafts/state", json={"ids": [a_old], "state": "removed", "note": "违规被删:平台提示营销"})
    u.post("/api/drafts/state", json={"ids": [a_mid], "state": "unsent"})
    d_old = db.q_one("SELECT published, published_at, pub_state, pub_state_note, pub_state_at FROM drafts WHERE id=?", a_old)
    d_mid = db.q_one("SELECT published, published_at, pub_state FROM drafts WHERE id=?", a_mid)
    rep.check("标「被平台删除」:发布状态保留(发布过),记下原因和时间;标「没发出去」:回到未发布、发布日期清掉",
              d_old["pub_state"] == "removed" and d_old["published"] == 1 and "违规被删" in d_old["pub_state_note"] and d_old["pub_state_at"]
              and d_mid["pub_state"] == "unsent" and d_mid["published"] == 0 and d_mid["published_at"] == "", str((d_old, d_mid)))
    r1 = rows()
    ov1 = loop.overview(loop.evaluate(register=False))
    pub_after = next(x["n"] for x in ov1["funnel"] if x["key"] == "published")
    rep.check("标了之后:稿子的分级变成「被平台删除 / 没发出去」,不再算已发布、不再算「标了发布但没对上」(两篇都是原来算已发布的,已发布数和绑定提醒的总数各少 2),概览里有各自的数量",
              r1[a_old]["tier"] == "removed" and r1[a_mid]["tier"] == "unsent" and not r1[a_old]["published"] and r1[a_old]["tier_name"] == "被平台删除"
              and pub_after == pub_before - 2 and ov1["bind_todo"]["total"] == todo_before - 2 and ov1["tiers"]["removed"] >= 1 and ov1["tiers"]["unsent"] >= 1
              and a_old not in {x["id"] for x in api_user._loop_rows(loop.evaluate(register=False), "nodata")}, str((pub_before, pub_after, todo_before, ov1["bind_todo"]["total"])))

    # ---- 复盘页的三个列表 ----
    pg = u.get("/api/loop/page?reg=nodata&size=100").json()
    ids = [x["id"] for x in pg["reg"]["items"]]
    mine = [x for x in pg["reg"]["items"] if x["id"] in (a_new,)]
    rep.check("「标了发布但没对上」:不含已处理的;按登记天数从久到近排;超期的带 stale 标记;总数里有「超期」和「已处理」的数量",
              a_old not in ids and a_mid not in ids and a_new in ids and pg["reg"]["done"] >= 2 and pg["reg"]["stale_days"] == 2
              and all(ids.index(x) < ids.index(a_new) for x in ids if (next(i for i in pg["reg"]["items"] if i["id"] == x)["days"] or 0) > 0 and x != a_new)
              and mine and mine[0]["stale"] is False, str(ids[:6]))
    pgd = u.get("/api/loop/page?reg=done&size=100").json()
    dm = {x["id"]: x for x in pgd["reg"]["items"]}
    rep.check("「已处理」:列出被删 / 没发出去的稿子,带原因、时间、帖子类型、命中过的风险词(违规样本)",
              a_old in dm and a_mid in dm and "违规被删" in dm[a_old]["state_note"] and dm[a_old]["state_at"] and dm[a_mid]["ptype"] == "产品软文"
              and any("最好" in h for h in dm[a_mid]["hits"]) and dm[a_mid]["tier"] == "unsent", str(dm.get(a_mid)))
    stale_item = u.get("/api/loop/page?reg=nodata&size=100").json()["reg"]["items"]
    a_5 = mk("状态测试戊:登记五天且没标", 1, day(5))
    pg2 = u.get("/api/loop/page?reg=nodata&size=100").json()
    it5 = next(x for x in pg2["reg"]["items"] if x["id"] == a_5)
    rep.check("登记 5 天还没对上的:stale = True,排在今天才登记的前面;stale 总数包含它", it5["stale"] is True and [x["id"] for x in pg2["reg"]["items"]].index(a_5) < [x["id"] for x in pg2["reg"]["items"]].index(a_new) and pg2["reg"]["stale"] >= 1)

    # ---- 成品库带状态 ----
    lib = {x["id"]: x for x in api_user._library()}
    rep.check("成品库里带 pub_state / 原因", lib[a_old]["pub_state"] == "removed" and "违规被删" in lib[a_old]["pub_state_note"] and lib[a_todo]["pub_state"] == "")

    # ---- 状态作废:重新登记发布 / 千帆里其实有 ----
    api_user.set_published(a_mid, api_user.PubIn(on=True))
    rep.check("老的标记发布接口重新标记发布:之前的「没发出去」作废", db.q_one("SELECT pub_state, published FROM drafts WHERE id=?", a_mid)["pub_state"] == "" and rows()[a_mid]["tier"] == "nodata")
    u.post("/api/drafts/state", json={"ids": [a_old], "state": "removed", "note": "x"})
    u.post("/api/drafts/publish", json={"ids": [a_old], "on": True, "published_at": day(0)})
    rep.check("登记接口重新登记发布:之前的「被平台删除」作废", db.q_one("SELECT pub_state FROM drafts WHERE id=?", a_old)["pub_state"] == "" and rows()[a_old]["tier"] == "nodata")
    t_match = mk("状态测试己:千帆里其实有这篇的标题", 1, day(2))
    u.post("/api/drafts/state", json={"ids": [t_match], "state": "removed", "note": "标错了"})
    qianfan.ingest({"kind": "notes", "shop_id": "pstest", "shop_name": "状态店", "items": [{"note_id": "PS1", "title": "状态测试己:千帆里其实有这篇的标题", "views": 40, "likes": 2, "collects": 1, "comments": 0, "published_at": day(1)}]})
    still = loop.evaluate(register=False)
    rr = {r["id"]: r for r in still["rows"]}[t_match]
    rep.check("标成被删,但千帆里其实找得到这篇笔记:以千帆为准(这一轮就按正常稿子算,不显示被删)", rr["tier"] != "removed" and rr["views"] == 40, str(rr["tier"]))
    loop.evaluate(register=True)
    rep.check("评估(登记模式)时顺手把标错的状态清掉,以后不再误显示", db.q_one("SELECT pub_state FROM drafts WHERE id=?", t_match)["pub_state"] == "")
    # 恢复
    u.post("/api/drafts/state", json={"ids": [a_todo], "state": "unsent"})
    u.post("/api/drafts/state", json={"ids": [a_todo], "state": ""})
    rep.check("恢复:状态、原因都清空,回到未发布", db.q_one("SELECT pub_state, pub_state_note, published FROM drafts WHERE id=?", a_todo) == {"pub_state": "", "pub_state_note": "", "published": 0})

    # ---- 页面 ----
    from pathlib import Path
    flow = Path("static/flow.js").read_text(encoding="utf-8")
    rep.check("页面:复盘「发布登记」有「已处理」一栏;没对上的显示登记天数和核对提示,带「没发出去 / 被平台删除 / 取消标记」三个按钮;被删弹窗有原因选项;预览里有恢复",
              'data-rtab="done"' in flow and "data-unsent" in flow and "data-removed" in flow and "data-restore" in flow and "function stateDialog" in flow and "STATE_REASONS" in flow
              and "千帆里还没找到:请核对" in flow and "data-pstate-clear" in flow)
    db.q_exec("DELETE FROM drafts WHERE project_id=?", pj)
