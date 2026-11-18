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
        "INSERT INTO drafts(project_id,title,content,status,published,published_at,ptype,checks,account_id,account_name) VALUES(?,?,?,'ready',?,?,'产品软文',?,'acct_ps','状态号')", pj, title, "正文", pub, pday,
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
    # ---- 系统自动判断:标了发布超过 7 天、千帆同步后还是找不到 → 多半没发出去 ----
    x_old = mk("自动判断测试甲:标了十天找不到", 1, day(10))
    x_new = mk("自动判断测试乙:标了两天找不到", 1, day(2))
    x_cand = mk("自动判断测试戊:标了六天,但这几天账号有没认领的笔记", 1, day(6))
    x_b = mk("自动判断测试己:另一个账号标了四天", 1, day(4))
    db.q_exec("UPDATE drafts SET account_id='acct_ps2', account_name='对账号二' WHERE id=?", x_b)
    x_c3 = mk("自动判断测试庚:第三个账号标了一篇", 1, day(2))
    db.q_exec("UPDATE drafts SET account_id='acct_ps3', account_name='对账号三' WHERE id=?", x_c3)
    x_noacc = mk("自动判断测试丙:账号没采集", 1, day(10))
    db.q_exec("UPDATE drafts SET account_id='acct_none', account_name='没采集的号' WHERE id=?", x_noacc)
    x_back = mk("自动判断测试丁:十天前标的但笔记其实在", 1, day(10))
    qianfan.ingest({"kind": "notes", "shop_id": "pstest2", "shop_name": "自动判断店", "items": [
        {"note_id": "AU1", "title": "自动判断测试丁:十天前标的但笔记其实在", "views": 30, "likes": 2, "collects": 1, "comments": 0, "published_at": day(9), "account_id": "acct_ps", "account_name": "状态号"},
        {"note_id": "AU2", "title": "这是这个账号别的笔记和稿子无关", "views": 30, "likes": 2, "collects": 1, "comments": 0, "published_at": day(1), "account_id": "acct_ps", "account_name": "状态号"},
        {"note_id": "AU4", "title": "完全不一样的标题,但就在标记那几天发的", "views": 30, "likes": 2, "collects": 1, "comments": 0, "published_at": day(5), "account_id": "acct_ps", "account_name": "状态号"},
        {"note_id": "AU6", "title": "第三个账号的第一篇笔记", "views": 30, "likes": 2, "collects": 1, "comments": 0, "published_at": day(2), "account_id": "acct_ps3", "account_name": "对账号三"},
        {"note_id": "AU7", "title": "第三个账号的第二篇笔记", "views": 30, "likes": 2, "collects": 1, "comments": 0, "published_at": day(1), "account_id": "acct_ps3", "account_name": "对账号三"},
        {"note_id": "AU5", "title": "另一个账号很久以前的笔记", "views": 30, "likes": 2, "collects": 1, "comments": 0, "published_at": day(30), "account_id": "acct_ps2", "account_name": "对账号二"}]})
    rec_ev = loop.evaluate(register=False)
    rec_ = {a["account_id"]: a for a in loop.reconcile(rec_ev)}
    rep.check("账号对账:标了没对上的比没认领的笔记多 → 「至少 N 篇没发出去」(bad);没认领的笔记不少于标了没对上的 → 「可能是改了标题 / 封面发的」(warn);没有任何没认领的笔记且标满 3 天 → 「多半没发出去」(bad);N / K / U / F 数量对得上",
              rec_["acct_ps"]["kind"] == "bad" and "至少" in rec_["acct_ps"]["verdict"] and rec_["acct_ps"]["F"] == 2 and rec_["acct_ps"]["U"] >= 3
              and rec_["acct_ps3"]["kind"] == "warn" and rec_["acct_ps3"]["U"] == 1 and rec_["acct_ps3"]["F"] == 2 and rec_["acct_ps3"]["N"] == 2
              and rec_["acct_ps2"]["kind"] == "bad" and rec_["acct_ps2"]["U"] == 1 and rec_["acct_ps2"]["F"] == 0 and rec_["acct_ps2"]["N"] == 0, str((rec_.get("acct_ps"), rec_.get("acct_ps2"), rec_.get("acct_ps3"))))
    rep.check("对账带简短判断(brief)和「至少几篇没发出去」(short):多半没发 = 全部,标得比没认领的多 = 差额,可能改版 = 0",
              rec_["acct_ps2"]["short"] == 1 and rec_["acct_ps"]["short"] == rec_["acct_ps"]["U"] - rec_["acct_ps"]["F"] and rec_["acct_ps3"]["short"] == 0
              and all(a_.get("brief") for a_ in rec_.values()), str({k: (v["short"], v["brief"]) for k, v in rec_.items()}))
    pg_r = u.get("/api/loop/page?reg=nodata&size=100&racc=acct_ps3").json()["reg"]
    pg_all = u.get("/api/loop/page?reg=nodata&size=100").json()["reg"]
    rep.check("账号对账「去核对」:没对上列表可以只看某个账号(racc),不带则看全部", pg_r["items"] and all(x["account_id"] == "acct_ps3" for x in pg_r["items"]) and pg_all["total"] > pg_r["total"], str((pg_r["total"], pg_all["total"])))
    loop.evaluate(register=True)
    st = lambda i: db.q_one("SELECT published, published_at, pub_state, pub_state_note FROM drafts WHERE id=?", i)
    s_old, s_new, s_na, s_bk, s_cd, s_b = st(x_old), st(x_new), st(x_noacc), st(x_back), st(x_cand), st(x_b)
    rep.check("自动判断:标了 10 天、窗口(标记日前 2 天到后 3 天)已过完、账号这几天没有任何没认领的笔记 → 系统标成「没发出去」并写明「系统判断」;标了 2 天的(窗口没过完)、账号没采集的不动;窗口里有没认领笔记的(可能是改了标题发的)不判;笔记其实在的按笔记对上(已发布)",
              s_old["pub_state"] == "unsent" and s_old["published"] == 0 and s_old["pub_state_note"].startswith("系统判断") and s_new["pub_state"] == "" and s_new["published"] == 1
              and s_na["pub_state"] == "" and s_na["published"] == 1 and s_bk["pub_state"] == "" and s_bk["published"] == 1
              and s_cd["pub_state"] == "" and s_cd["published"] == 1 and s_b["pub_state"] == "unsent", str((s_old, s_new, s_na, s_bk, s_cd, s_b)))
    pgd2 = u.get("/api/loop/page?reg=done&size=100").json()
    dm2 = {x["id"]: x for x in pgd2["reg"]["items"]}
    rep.check("自动判断的稿子出现在「已处理」里,带原因(系统判断),可以恢复", x_old in dm2 and dm2[x_old]["state_note"].startswith("系统判断") and dm2[x_old]["tier"] == "unsent")
    qianfan.ingest({"kind": "notes", "shop_id": "pstest2", "shop_name": "自动判断店", "items": [
        {"note_id": "AU3", "title": "自动判断测试甲:标了十天找不到", "views": 12, "likes": 1, "collects": 0, "comments": 0, "published_at": day(8), "account_id": "acct_ps", "account_name": "状态号"}]})
    loop.evaluate(register=True)
    s_old2 = st(x_old)
    rep.check("以后千帆里找到了这篇:系统判断自动恢复,按笔记登记为已发布(发布日期取笔记的)", s_old2["pub_state"] == "" and s_old2["published"] == 1 and s_old2["published_at"] == day(8), str(s_old2))
    ov_s = loop.review_steps(loop.evaluate(register=False))
    rep.check("近 7 天发布:每天分开算「千帆里已找到」和「只是标了、还没找到」(claimed);复盘数据里带账号对账(recon)", all("claimed" in d_ and d_["claimed"] <= d_["n"] for d_ in ov_s["pub"]["days"]) and "claimed" in ov_s["pub"] and isinstance(ov_s["recon"], list))
    db.q_exec("DELETE FROM qf_snapshots WHERE shop_id='pstest2'")
    rep.check("页面:账号对账并进「近 7 天发布」卡(默认只一行摘要,点开看明细,只列有问题的账号,对得上的合成一行),有「去核对」;柱状图每天两根并排(已找到 / 只标了发布)、同一刻度、有图例",
              "data-recon" in flow and "data-gocheck" in flow and "data-rracc-clear" in flow and "lp-legend" in flow and "另有 ${good.length} 个账号对得上" in flow
              and ".lp-pair" in Path("static/style.css").read_text(encoding="utf-8") and "lp-b1" in flow and "lp-bt" not in flow and "lp-rsum" in flow)
    rep.check("页面:预览里标记发布只有底部一个按钮(面板里不再放「登记已发布」),全站统一叫「标记 / 已发布」,不再出现「登记已发布 / 发布登记」",
              "登记已发布" not in flow and "发布登记" not in flow and "data-ppub>" in flow and flow.count("data-ppub>") == 1 and "没标记发布的" in flow and "发布标记" in flow)
    rep.check("页面:新建任务的发布账号选填(可以不指定,发布时再选);标记已发布前必须有发布账号(预览和批量);复盘「发布标记」默认看「没对上」,「没标记发布的」排在最后",
              "data-facc=\"\">不指定" in flow and "先选打算发到哪个账号" not in flow and "xhs_last_acct" not in flow and "function needAcct" in flow and "没指定发布账号" in flow
              and 'reg: "nodata"' in flow and flow.index('data-rtab="nodata"') < flow.index('data-rtab="done"') < flow.index('data-rtab="todo"'))
    db.q_exec("DELETE FROM drafts WHERE project_id=?", pj)
