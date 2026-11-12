"""爆款回流:稿子对上千帆数据 → 判定爆款 → 漏斗和复盘 → 以爆款为样本继续复刻 → 自动继续复刻。
放在人员管理之前跑;结束时清掉自己造的数据和设置。"""
import datetime
import json

from ..common import _raises2


def run(rep, env):
    from app import db, loop, qianfan as qf, worker
    user, q = env.user, env.q

    print("\n[爆款回流]")
    day = lambda n: (datetime.date.today() - datetime.timedelta(days=n)).isoformat()
    db.save_settings({"deepseek_api_key": "", "loop_win_lift": "2", "loop_min_eng": "20", "loop_min_days": "3", "loop_spawn_count": "3",
                      "loop_auto": "0", "loop_daily_cap": "10"})
    acc = "acc_loop"
    pid = db.q_exec("INSERT INTO projects(track,keywords,direction,account_id,account_name,created_by) VALUES('美食','[\"闭环\"]','原方向',?,?,'zhang')", acc, "闭环号").lastrowid
    mk = lambda title, **kw: db.q_exec("INSERT INTO drafts(project_id,title,content,angle,status,account_id,account_name,auto_ok,published) VALUES(?,?,?,?,'ready',?,?,?,?)",
                                       pid, title, "正文" * 30, kw.get("angle", "黄金奶做法:写给上班族"), acc, "闭环号", 1, kw.get("pub", 0)).lastrowid
    d_win, d_good, d_weak, d_new, d_nodata, d_off, d_win2 = (mk("闭环爆款稿甲"), mk("闭环不错稿乙"), mk("闭环偏弱稿丙"), mk("闭环刚发稿丁"), mk("闭环没数据稿戊", pub=1), mk("闭环未发布稿己"), mk("闭环爆款稿庚"))
    filler = [{"note_id": f"f{i}", "title": f"闭环账号普通笔记{i}号", "published_at": day(30), "views": 300, "likes": 6, "collects": 2, "comments": 2, "account_id": acc, "account_name": "闭环号"} for i in range(30)]
    mine = [("闭环爆款稿甲", 10, 60, 30, 10), ("闭环不错稿乙", 10, 15, 5, 2), ("闭环偏弱稿丙", 10, 2, 0, 0), ("闭环刚发稿丁", 0, 90, 50, 10), ("闭环爆款稿庚", 8, 50, 20, 10)]
    filler.append({"note_id": "x1", "title": "改过标题的那篇笔记", "published_at": day(5), "views": 2000, "likes": 30, "collects": 10, "comments": 5, "account_id": acc, "account_name": "闭环号"})
    items = filler + [{"note_id": f"m{i}", "title": t, "published_at": day(a), "views": 2000, "likes": l, "collects": c, "comments": m, "account_id": acc, "account_name": "闭环号"} for i, (t, a, l, c, m) in enumerate(mine)]
    qf.ingest({"kind": "notes", "shop_id": "shop_loop", "items": items})

    ev = {r["id"]: r for r in loop.evaluate()["rows"]}
    tier = lambda d: ev[d]["tier"]
    rep.check("判定:互动远高于账号平均且够久 = 爆款", tier(d_win) == "winner" and tier(d_win2) == "winner" and (ev[d_win]["lift"] or 0) >= 2, str(ev[d_win]))
    rep.check("判定:略高于平均 = 不错;明显低于平均 = 偏弱", tier(d_good) == "good" and tier(d_weak) == "weak", f"{tier(d_good)} {ev[d_good]['lift']} {tier(d_weak)} {ev[d_weak]['lift']}")
    rep.check("判定:刚发布的数据没沉淀,标观察中,不下结论", tier(d_new) == "observing", tier(d_new))
    rep.check("判定:标了已发布但千帆里没对上 = 没对上数据;没发布 = 未发布", tier(d_nodata) == "nodata" and tier(d_off) == "unpublished")
    rep.check("判定:互动下限挡住小数据(几个赞不算爆款)", loop.classify({"likes": 5, "collects": 0, "comments": 0, "views": 50, "published_at": day(20)}, {"published": 1, "published_at": ""}, ("eng", 1.0), loop.cfg())[0] != "winner")
    rep.check("对上数据的稿子自动登记为已发布", db.q_one("SELECT published FROM drafts WHERE id=?", d_win)["published"] == 1)
    rep.check("账号基线取去掉最高 5% 的平均,几篇爆款不拉高门槛", loop._baselines(items)[acc][0] == "eng")

    # 发布登记:手动对上千帆笔记、批量标记已发布
    cand = user.get(f"/api/loop/notes?draft_id={d_nodata}").json()["items"]
    ids_ = [x["note_id"] for x in cand]
    rep.check("手动对号候选:只列还没对上稿子的千帆笔记,同账号的排前面", "x1" in ids_ and "m0" not in ids_ and cand[0]["same_account"], str(ids_[:5]))
    rep.check("手动对号候选:能按标题搜索", [x["note_id"] for x in user.get(f"/api/loop/notes?q={'改过标题'}").json()["items"]] == ["x1"])
    rep.check("手动对号:千帆里没有这篇笔记时拒绝", user.post(f"/api/drafts/{d_nodata}/link", json={"note_id": "nope"}).status_code == 400)
    rep.check("手动对号:成功", user.post(f"/api/drafts/{d_nodata}/link", json={"note_id": "x1"}).status_code == 200)
    ev3 = {r["id"]: r for r in loop.evaluate()["rows"]}
    rep.check("手动对号后:按真实数据判定,发布日期取笔记的日期", ev3[d_nodata]["tier"] == "winner" and ev3[d_nodata]["linked"] and db.q_one("SELECT published_at FROM drafts WHERE id=?", d_nodata)["published_at"] == day(5), str(ev3[d_nodata]))
    rep.check("手动对上的笔记不再出现在候选里", "x1" not in [x["note_id"] for x in user.get(f"/api/loop/notes?draft_id={d_off}").json()["items"]])
    user.post(f"/api/drafts/{d_nodata}/link", json={"note_id": ""})
    rep.check("取消对应后回到没对上数据", {r["id"]: r for r in loop.evaluate()["rows"]}[d_nodata]["tier"] == "nodata")
    rep.check("批量登记:日期格式不对 / 没选稿子拒绝", user.post("/api/drafts/publish", json={"ids": [d_off], "published_at": "昨天"}).status_code == 400 and user.post("/api/drafts/publish", json={"ids": []}).status_code == 400)
    r_ = user.post("/api/drafts/publish", json={"ids": [d_off], "on": True, "published_at": day(2)})
    row_ = db.q_one("SELECT published, published_at FROM drafts WHERE id=?", d_off)
    rep.check("批量登记:标记已发布并记下补填的发布日期", r_.status_code == 200 and row_["published"] == 1 and row_["published_at"] == day(2), str(row_))
    user.post("/api/drafts/publish", json={"ids": [d_off], "on": False})
    rep.check("批量登记:可以取消", db.q_one("SELECT published, published_at FROM drafts WHERE id=?", d_off) == {"published": 0, "published_at": ""})

    ov = loop.overview()
    fun = {f["key"]: f["n"] for f in ov["funnel"]}
    rep.check("复盘漏斗:已发布 / 数据回流 / 爆款", fun["data"] >= 5 and fun["winner"] >= 2 and fun["published"] >= fun["data"], str(ov["funnel"]))
    rep.check("复盘:按主题汇总爆款率", any(x["name"] == "黄金奶做法" and x["winners"] >= 2 for x in ov["by_topic"]), str(ov["by_topic"]))
    db.q_exec("UPDATE drafts SET cover_card=? WHERE id=?", json.dumps({"layout": "photo_only", "kind": "photo"}), d_win)
    db.q_exec("UPDATE drafts SET cover_card=? WHERE id=?", json.dumps({"layout": "split_top"}), d_win2)   # 老稿没记 kind:按版式推出来
    db.q_exec("UPDATE drafts SET cover_card=? WHERE id=?", json.dumps({"layout": "quote", "kind": "html"}), d_good)
    ev_c = {r["id"]: r for r in loop.evaluate(register=False)["rows"]}
    ov_c = loop.overview()
    rep.check("复盘:每篇稿子带封面类型(新稿记的 / 老稿按版式推)", ev_c[d_win]["cover_kind"] == "photo" and ev_c[d_win2]["cover_kind"] == "photo_text" and ev_c[d_good]["cover_kind"] == "html"
              and ev_c[d_weak]["cover_name"] == "(旧稿没记录)", str({k: ev_c[k]["cover_kind"] for k in (d_win, d_win2, d_good, d_weak)}))
    rep.check("复盘:按封面类型统计爆款率", {x["name"] for x in ov_c["by_cover"]} >= {"纯产品图", "实拍图 + 排字"} and next(x for x in ov_c["by_cover"] if x["name"] == "纯产品图")["winners"] == 1, str(ov_c["by_cover"]))
    rep.check("复盘:继续复刻对比有两组", set(ov["spawn_effect"]) == {"original", "spawned"} and ov["spawn_effect"]["original"]["winners"] >= 2)

    # 继续复刻:新项目 + 样本进笔记库 + 拆解卡 + 流水线从「拆解」阶段开始
    calls = []
    orig = worker.submit_playbook
    worker.submit_playbook = lambda pb: calls.append(pb)
    try:
        np = loop.spawn(d_win, 4, by="lisi")
        p = db.q_one("SELECT * FROM projects WHERE id=?", np)
        run_ = db.q_one("SELECT * FROM autoruns WHERE project_id=?", np)
        note = db.q_one("SELECT * FROM notes WHERE uid=?", f"ours:{d_win}")
        pb = db.q_one("SELECT * FROM playbooks WHERE project_id=?", np)
        rep.check("继续复刻:新任务记录来源稿、第 1 代、发起人,并沿用账号和赛道", p["spawn_from"] == d_win and p["generation"] == 1 and p["created_by"] == "lisi" and p["account_id"] == acc and p["track"] == "美食", str(p))
        rep.check("继续复刻:方向里带上爆款标题和「不要照抄」", "闭环爆款稿甲" in p["direction"] and "不要照抄" in p["direction"] and "原方向" in p["direction"], p["direction"])
        rep.check("继续复刻:爆款稿进笔记库当样本,带真实互动数据", note and note["source"] == "ours" and note["likes"] == 60 and note["collects"] == 30, str(note and note["likes"]))
        rep.check("继续复刻:拆解卡以它为样本,并提交拆解", pb and note["id"] in pb["note_ids"] and calls == [pb["id"]], str(pb))
        rep.check("继续复刻:流水线从拆解阶段开始,目标篇数", run_["status"] == "analyzing" and run_["target"] == 4 and run_["state"]["spawn_from"] == d_win, str(run_))
        rep.check("继续复刻:这一轮没跑完时不能重复开", _raises2(lambda: loop.spawn(d_win)))
        rep.check("继续复刻:没内容的稿子拒绝", _raises2(lambda: loop.spawn(db.q_exec("INSERT INTO drafts(project_id,title,status) VALUES(?,'','queued')", pid).lastrowid)))
        db.q_exec("UPDATE projects SET generation=2 WHERE id=?", pid)   # 来源任务本身是第 2 代
        again = loop.spawn(d_win2)   # 另一篇爆款再开一轮,代数按来源项目的代数算
        g3 = loop.spawn(d_good, 1)
        rep.check("继续复刻:代数 = 来源稿所在任务的代数 + 1", db.q_one("SELECT generation FROM projects WHERE id=?", g3)["generation"] == 3 and db.q_one("SELECT generation FROM projects WHERE id=?", again)["generation"] == 3)
        ev2 = {r["id"]: r for r in loop.evaluate()["rows"]}
        rep.check("复盘:爆款稿显示已复刻过几轮", ev2[d_win]["spawned"] == 1 and ev2[d_win2]["spawned"] == 1)

        # 接口
        rep.check("接口:复盘概览和稿子列表", user.get("/api/loop/overview").status_code == 200 and any(r["tier"] == "winner" for r in user.get("/api/loop/drafts?tier=winner").json()["items"]))
        pg1 = user.get("/api/loop/page?size=2&regpage=1&reg=todo").json()
        pg9 = user.get("/api/loop/page?size=2&page=99&regpage=99").json()
        allrows = user.get("/api/loop/drafts").json()["items"]
        rep.check("接口:复盘分页,一次取齐概览/爆款/列表", pg1["overview"]["funnel"] and pg1["rows"]["total"] == len(allrows) and len(pg1["rows"]["items"]) == min(2, len(allrows))
                  and all(w["tier"] == "winner" for w in pg1["winners"]) and pg1["reg"]["total"] == pg1["overview"]["tiers"]["unpublished"])
        rep.check("接口:发布登记这一页的稿子带封面字段(卡片显示用)", pg1["reg"]["items"] and all("cover" in x for x in pg1["reg"]["items"]))
        rep.check("接口:复盘分页,页码越界会落到最后一页", pg9["rows"]["page"] == max(1, -(-len(allrows) // 2)) and len(pg9["rows"]["items"]) >= 1 and pg9["reg"]["page"] >= 1)
        rep.check("接口:没配 DeepSeek Key 时不能继续复刻", user.post(f"/api/drafts/{d_win}/spawn", json={"count": 3}).status_code == 400)
        db.save_settings({"deepseek_api_key": "k"})
        lib = {x["id"]: x for x in user.get("/api/library").json()}
        rep.check("成品库带上爆款判定", lib[d_win]["tier"] == "winner" and lib[d_good]["tier"] == "good")
        rep.check("成品库带上发布日期和是否手动对号(成品预览的发布登记用)", lib[d_win]["published_at"] == day(10) and lib[d_win]["linked"] is False and lib[d_off]["published"] is False, str({k: lib[d_win][k] for k in ("published_at", "linked")}))
        db.q_exec("UPDATE autoruns SET status='done' WHERE project_id IN (SELECT id FROM projects WHERE spawn_from=?)", d_win)
        r = user.post(f"/api/drafts/{d_win}/spawn", json={"count": 2})
        rep.check("接口:配了 Key 后可以继续复刻,返回新任务", r.status_code == 200 and db.q_one("SELECT target FROM autoruns WHERE project_id=?", r.json()["id"])["target"] == 2, r.text)

        # 自动继续复刻:默认关;打开后只复刻还没复刻过的爆款,受每日上限约束,最多 3 代
        d_win3 = mk("闭环爆款稿辛")
        qf.ingest({"kind": "notes", "shop_id": "shop_loop", "items": items + [{"note_id": "m9", "title": "闭环爆款稿辛", "published_at": day(9), "views": 2000, "likes": 70, "collects": 20, "comments": 5, "account_id": acc, "account_name": "闭环号"}]})
        db.q_exec("UPDATE autoruns SET status='done'")
        rep.check("自动复刻:默认关闭,不会自己花钱", loop.auto_tick(force=True) == 0)
        db.save_settings({"loop_auto": "1", "loop_daily_cap": "0"})
        rep.check("自动复刻:每日上限为 0 时不复刻", loop.auto_tick(force=True) == 0)
        db.save_settings({"loop_daily_cap": "50", "loop_spawn_count": "2"})
        n = loop.auto_tick(force=True)
        rep.check("自动复刻:只复刻还没复刻过的新爆款", n == 1 and db.q_one("SELECT created_by FROM projects WHERE spawn_from=?", d_win3)["created_by"] == "自动", str(n))
        rep.check("自动复刻:再跑一次没有新爆款就不重复", loop.auto_tick(force=True) == 0)
    finally:
        worker.submit_playbook = orig
        db.save_settings({"loop_auto": "0", "deepseek_api_key": ""})
        # 清理:本用例造的项目、稿子、样本笔记、快照
        pids = [r["id"] for r in db.q_all("SELECT id FROM projects WHERE id=? OR spawn_from IS NOT NULL", pid)]
        for t in ("autoruns", "playbooks", "project_notes", "drafts"):
            db.q_exec(f"DELETE FROM {t} WHERE project_id IN ({','.join('?' * len(pids))})", *pids)
        db.q_exec("DELETE FROM projects WHERE id IN (%s)" % ",".join("?" * len(pids)), *pids)
        db.q_exec("DELETE FROM notes WHERE source='ours'")
        db.q_exec("DELETE FROM qf_snapshots WHERE shop_id='shop_loop'")
