"""员工端笔记库、多店铺、示例数据、热门笔记沉淀任务。"""
from app import db


def run(rep, env):
    aqf, col, q, qf = env.aqf, env.col, env.q, env.qf
    # 员工端笔记库(只读):只给看笔记需要的字段,能按主题筛选和搜索
    from app import api_user as au_nb
    all_nb = au_nb.notebase(limit=500)
    rep.check("员工端笔记库:有笔记,字段只含看笔记需要的(没有原始数据、采集端信息)", all_nb["total"] > 0 and all(set(i) == set(au_nb.NB_FIELDS) for i in all_nb["items"]) and not ({"raw", "worker", "ai_error"} & set(all_nb["items"][0])))
    topic_nb = next((i["topic"] for i in all_nb["items"] if i["topic"]), "")
    by_topic_nb = au_nb.notebase(topic=topic_nb, limit=500)
    rep.check("员工端笔记库:按主题筛选", topic_nb != "" and 0 < by_topic_nb["total"] <= all_nb["total"] and all(i["topic"] == topic_nb for i in by_topic_nb["items"]), topic_nb)
    t1_nb = all_nb["items"][0]["title"]
    rep.check("员工端笔记库:按标题搜索;每页最多 60 篇", any(i["title"] == t1_nb for i in au_nb.notebase(q=t1_nb[:6])["items"]) and len(au_nb.notebase(limit=9999)["items"]) <= 60)
    fa_nb = au_nb.notebase_facets()
    rep.check("员工端笔记库:筛选项不含空值", set(fa_nb) == {"tracks", "topics", "types", "tiers"} and all(x["k"] for v in fa_nb.values() for x in v))
    # 多店铺:数据按店铺分开存;第一个店铺认领以前没分店铺的数据;任务按店铺下发和领取
    db.q_exec("DELETE FROM qf_snapshots")
    db.q_exec("DELETE FROM qf_shops")
    db.q_exec("DELETE FROM crawl_tasks WHERE type='qianfan'")
    qf.ingest({"kind": "notes", "items": [{"title": "老数据笔记", "likes": 1, "account_id": "a1", "account_name": "甲"}]})
    qf.ingest({"kind": "account", "account": {"name": "甲", "fans": 10, "account_id": "a1"}})
    qf.register_shop("S1", "店一")
    rep.check("多店铺:第一个店铺认领以前没分店铺的数据", qf.latest("notes", "", "", "S1") is not None and qf.latest("notes") is None and qf.series(5, "a1")[-1]["shop_id"] == "S1")
    qf.register_shop("S2", "店二")
    rep.check("多店铺:第二个店铺不会认领别人的数据", qf.latest("notes", "", "", "S2") is None and [x["shop_id"] for x in qf.shops()] == ["S1", "S2"])
    sh_state = {x["shop_id"]: x for x in qf.shops()}
    rep.check("多店铺:刚登记的店铺显示在线,没有同步过 / 没有待办", sh_state["S2"]["online"] and sh_state["S2"]["last_sync"] == "" and sh_state["S2"]["pending"] == 0 and sh_state["S1"]["last_sync"] != "")
    db.q_exec("UPDATE qf_shops SET last_seen=datetime('now','-10 minutes') WHERE shop_id='S1'")
    qf.add_task("account", "", "", "S1")
    sh_state = {x["shop_id"]: x for x in qf.shops()}
    rep.check("多店铺:采集端一阵子没来的店铺显示离线,并统计等着它的任务", not sh_state["S1"]["online"] and sh_state["S1"]["pending"] == 1 and sh_state["S2"]["online"], str(sh_state["S1"]))
    db.q_exec("DELETE FROM crawl_tasks WHERE type='qianfan'")
    qf.ingest({"kind": "notes", "shop_id": "S2", "shop_name": "店二", "items": [{"title": "二店笔记", "likes": 2, "account_id": "b1", "account_name": "乙"}]})
    qf.ingest({"kind": "account", "shop_id": "S2", "accounts": [{"account_id": "b1", "name": "乙", "fans": 99}, {"account_id": "b2", "name": "丙", "fans": 5}]})
    all_notes, _ = qf.notes_all()
    rep.check("多店铺:笔记表现各店铺各一份,合并时带 shop_id,可只取一个店铺", {i["title"]: i["shop_id"] for i in all_notes} == {"老数据笔记": "S1", "二店笔记": "S2"} and [i["title"] for i in qf.notes_all("S2")[0]] == ["二店笔记"])
    rep.check("多店铺:账号按店铺筛,带店铺名", [(a["account_id"], a["shop_name"]) for a in qf.accounts("S2")] == [("b1", "店二"), ("b2", "店二")] and [a["account_id"] for a in qf.accounts("S1")] == ["a1"])
    ov_s2 = aqf.overview(shop="S2")
    rep.check("多店铺:看板选一个店铺只看它的账号和笔记", ov_s2["shop"] == "S2" and {a["account_id"] for a in ov_s2["accounts"]} == {"b1", "b2"} and ov_s2["notes"]["total"] == 1 and len(ov_s2["shops"]) == 2, str(ov_s2["notes"])[:150])
    rep.check("多店铺:店铺不存在回到全部", aqf.overview(shop="nope")["shop"] == "")
    bo = aqf.board_overview(shop="S2")
    rep.check("员工端数据看板:只读接口和管理端同一份数据,不带同步设置", "settings" not in bo and {a["account_id"] for a in bo["accounts"]} == {"b1", "b2"} and bo["notes"]["total"] == 1)
    from app import auth as auth_bd
    paths_bd = {getattr(r, "path", "") for r in aqf.user.routes}
    rep.check("员工端数据看板:三个只读接口都挂在要登录的员工路由上", {"/api/qianfan/board/overview", "/api/qianfan/board/hot", "/api/qianfan/board/topics"} <= paths_bd and any(d.dependency is auth_bd.require_user for d in aqf.user.dependencies), str(sorted(paths_bd))[:200])
    qf.ingest({"kind": "hot_keywords", "shop_id": "S1", "period": "7d", "items": [{"rank": 1, "keyword": "一店词", "heat": 5}]})
    qf.ingest({"kind": "hot_keywords", "shop_id": "S2", "period": "7d", "items": [{"rank": 1, "keyword": "二店词", "heat": 9}]})
    rep.check("多店铺:热门榜各店铺各一份", aqf.hot("hot_keywords", "", "7d", "S1")["items"][0]["name"] == "一店词" and aqf.hot("hot_keywords", "", "7d", "S2")["items"][0]["name"] == "二店词")
    rep.check("多店铺:员工端热词合并各店铺", set(qf.hot_names("")["keywords"]) == {"一店词", "二店词"}, str(qf.hot_names("")))
    qf.ingest({"kind": "hot_topics", "shop_id": "S1", "period": "7d", "items": [{"rank": 1, "name": "平台话题", "heat": 1}]})
    rep.check("多店铺:平台话题榜不分店铺,哪个店铺都能看到", aqf.hot("hot_topics", "", "7d", "S2")["items"][0]["name"] == "平台话题")
    db.save_settings({"qf_enabled": "1", "qf_kinds": "account,notes,hot_notes,hot_topics", "qf_categories": "", "qf_periods": "7d"})
    n_sch = qf.schedule(force=True)
    rep.check("多店铺:每个店铺各下发账号/笔记/热门笔记任务,平台话题榜只一份", n_sch == 7 and qf.schedule(force=True) == 0, str(n_sch))
    db.q_exec("DELETE FROM crawl_tasks WHERE type='qianfan'")
    rep.check("多店铺:平台话题榜快照存在哪个店铺名下,调度都算有数据,不会反复下发", qf.schedule(kinds=["hot_topics"]) == 0)
    qf.schedule(force=True)
    col_s1 = col.get("/api/tasks?types=qianfan&worker=w-s1&limit=20&shop=S1&kinds=account,notes,hot_notes").json()["tasks"]
    rep.check("多店铺:采集端只领自己店铺的任务,不领自己做不了的类别", sorted(t["query"] for t in col_s1) == ["account", "hot_notes", "notes"] and all(t["params"]["shop"] == "S1" for t in col_s1), str(col_s1)[:200])
    col_s2 = col.get("/api/tasks?types=qianfan&worker=w-s2&limit=20&shop=S2").json()["tasks"]
    rep.check("多店铺:另一个店铺的采集端领到自己的,加上不分店铺的话题榜", sorted((t["query"], t["params"]["shop"]) for t in col_s2) == [("account", "S2"), ("hot_notes", "S2"), ("hot_topics", ""), ("notes", "S2")], str(col_s2)[:200])
    rep.check("多店铺:没声明店铺的老采集端领不到分店铺的任务", col.get("/api/tasks?types=qianfan&worker=old&limit=20").json()["tasks"] == [])
    col.get("/api/tasks?types=qianfan&worker=w-s3&limit=1&shop=S3&shop_name=店三&kinds=notes")
    rep.check("多店铺:新店铺的采集端第一次来就登记", "S3" in [x["shop_id"] for x in qf.shops()] and [x["name"] for x in qf.shops() if x["shop_id"] == "S3"] == ["店三"])
    db.q_exec("DELETE FROM qf_snapshots")
    db.q_exec("DELETE FROM qf_shops")
    db.q_exec("DELETE FROM crawl_tasks WHERE type='qianfan'")
    db.q_exec("DELETE FROM qf_snapshots WHERE kind IN ('account','notes')")
    # 示例数据可载入也可清除,不影响真实数据
    before = q("SELECT COUNT(*) c FROM qf_snapshots WHERE data NOT LIKE '%\"sample\": true%'")[0]["c"]
    qf.load_sample()
    rep.check("千帆:载入示例数据后有标记", qf.has_sample())
    qf.clear_sample()
    rep.check("千帆:清除示例数据只删示例", not qf.has_sample() and q("SELECT COUNT(*) c FROM qf_snapshots WHERE data NOT LIKE '%\"sample\": true%'")[0]["c"] == before)
    # 热门笔记 → 沉淀任务,已排队的不重复
    t1 = aqf.to_title_tasks(aqf.TitleTasksIn(titles=["热门标题甲", "热门标题甲", "热门标题乙"]))
    t2 = aqf.to_title_tasks(aqf.TitleTasksIn(titles=["热门标题甲"]))
    rep.check("千帆:热门笔记标题 → 沉淀任务,去重", t1["created"] == 2 and t2["created"] == 0, f"{t1} {t2}")
    db.q_exec("DELETE FROM crawl_tasks WHERE label='千帆热门'")
