"""千帆数据:规范化、采集端接口、排序、笔记表现与稿子对应、榜单保留、员工端、多账号。"""
from ..common import _raises2
from app import db
from fastapi.testclient import TestClient


def run(rep, env):
    app, col, q = env.app, env.col, env.q
    print("\n[千帆数据]")
    from app import qianfan as qf, api_qianfan as aqf
    from app.notes import num as num_qf
    db.save_settings({"deepseek_api_key": ""})   # 笔记表现入库后会自动调 AI 归类;测试里不能联网,关掉
    db.q_exec("DELETE FROM crawl_tasks WHERE type='qianfan'")
    db.q_exec("INSERT INTO drafts(keyword,title,status) VALUES('千帆测试','空气炸锅早餐 10分钟搞定','ready')")
    did_qf = q("SELECT id FROM drafts WHERE keyword='千帆测试'")[0]["id"]
    # 规范化:中文表头、"1.2万"、缺失字段不编造
    r = qf.normalize("account", {"account": {"粉丝数": "1.2万", "新增粉丝": 35, "昵称": "测试号"}})
    rep.check("千帆:账号数据认中文表头和\"1.2万\"", r["account"] == {"fans": 12000, "new_fans": 35, "name": "测试号"}, str(r))
    r = qf.normalize("notes", {"items": [{"标题": "空气炸锅早餐!!10分钟搞定", "点赞": 20, "收藏": "1千", "评论": 3}, {"点赞": 5}]})
    rep.check("千帆:笔记表现没标题的行丢掉,互动数 = 赞+藏+评", len(r["items"]) == 1 and r["items"][0]["engagement"] == 20 + num_qf("1千") + 3, str(r))
    rep.check("千帆:热门话题去掉 # 号并按排名排序", [i["name"] for i in qf.normalize("hot_topics", {"items": [{"rank": 2, "话题": "#b"}, {"rank": 1, "话题": "#a"}]})["items"]] == ["a", "b"])
    rep.check("千帆:空数据拒绝", _raises2(lambda: qf.normalize("hot_notes", {"items": []})) and _raises2(lambda: qf.normalize("account", {})))
    # 采集端接口:领任务 → 回传 → 任务完成
    db.save_settings({"qf_enabled": "1", "qf_kinds": "account,hot_topics", "qf_categories": "美食,穿搭", "qf_periods": "7d", "library_collect_enabled": "1"})
    n_new = qf.schedule()
    rep.check("千帆:按设置下发任务(账号 1 个 + 话题榜 2 个品类)", n_new == 3, str(n_new))
    rep.check("千帆:有未完成任务时不重复下发", qf.schedule() == 0)
    rep.check("千帆:没声明 types 的旧采集端领不到千帆任务", all(t["type"] != "qianfan" for t in col.get("/api/tasks?worker=old").json()["tasks"]))
    got = col.get("/api/tasks?types=qianfan&worker=w1&limit=5").json()["tasks"]
    rep.check("千帆:声明 qianfan 的采集端领到任务,带 params", len(got) == 3 and all(t["type"] == "qianfan" and "period" in t["params"] for t in got), str(got)[:200])
    topic_t = next(t for t in got if t["query"] == "hot_topics" and t["params"]["category"] == "美食")
    resp = col.post("/api/qianfan/ingest", json={"task_id": topic_t["id"], "kind": "hot_topics", "period": "7d", "category": "美食", "items": [{"rank": 1, "name": "早餐打卡", "heat": 100}]})
    rep.check("千帆:回传成功并把任务标成完成", resp.status_code == 200 and resp.json()["stored"] == 1 and q("SELECT status FROM crawl_tasks WHERE id=?", topic_t["id"])[0]["status"] == "done", resp.text)
    acc_t = next(t for t in got if t["query"] == "account")
    col.post("/api/qianfan/ingest", json={"task_id": acc_t["id"], "kind": "account", "error": "登录过期"})
    rep.check("千帆:采集端报错 → 任务失败并记原因", q("SELECT status, error FROM crawl_tasks WHERE id=?", acc_t["id"])[0] == {"status": "failed", "error": "登录过期"})
    rep.check("千帆:刚失败的类别一小时内不重复下发", qf.schedule() == 0)
    rep.check("千帆:接口要采集端 Token", TestClient(app).post("/api/qianfan/ingest", json={}).status_code in (401, 403))
    rep.check("千帆:kind 不对返回 400", col.post("/api/qianfan/ingest", json={"kind": "x", "items": [1]}).status_code == 400)
    # 排序:用户任务 > 千帆 > 沉淀
    db.q_exec("DELETE FROM crawl_tasks WHERE status='pending'")
    db.q_exec("INSERT INTO crawl_tasks(type,query,purpose) VALUES('keyword','沉淀词','library')")
    qf.add_task("hot_notes", "", "7d")
    pj_qf = db.q_exec("INSERT INTO projects(track,keywords) VALUES('美食','[]')").lastrowid
    db.q_exec("INSERT INTO crawl_tasks(project_id,type,query,purpose) VALUES(?,'keyword','用户词','project')", pj_qf)
    order = [t["query"] for t in col.get("/api/tasks?types=keyword,qianfan&worker=w2&limit=5").json()["tasks"]]
    rep.check("千帆:领取顺序 用户任务 → 千帆同步 → 笔记库沉淀", order == ["用户词", "hot_notes", "沉淀词"], str(order))
    db.q_exec("DELETE FROM crawl_tasks WHERE status IN ('pending','running')")
    # 笔记表现 ↔ 我们的稿子
    qf.ingest({"kind": "notes", "items": [{"title": "空气炸锅早餐!!10分钟搞定", "likes": 30, "collects": 10, "comments": 2}, {"title": "完全无关的别家笔记", "likes": 1}]})
    ov = aqf.overview()
    rep.check("千帆:笔记表现按标题对上我们生成的稿子", ov["notes"]["matched"] == 1 and any(i.get("mine") for i in ov["notes"]["items"]), str(ov["notes"])[:200])
    # 榜单只保留最新 10 份,账号数据保留历史
    for i in range(12):
        qf.ingest({"kind": "hot_keywords", "period": "7d", "items": [{"rank": 1, "keyword": f"词{i}"}]})
    rep.check("千帆:榜单只留最近 10 份,看到的是最新一份", q("SELECT COUNT(*) c FROM qf_snapshots WHERE kind='hot_keywords'")[0]["c"] == 10 and aqf.hot("hot_keywords", "", "7d")["items"][0]["name"] == "词11")
    for i in range(3):
        qf.ingest({"kind": "account", "account": {"fans": 100 + i}})
    rep.check("千帆:账号数据留历史,趋势按时间排", [x["fans"] for x in qf.series()][-3:] == [100, 101, 102])
    # 员工端
    hn = qf.hot_names("")
    rep.check("千帆:员工端热词取最新榜单", hn["keywords"][:1] == ["词11"] and hn["topics"] == ["早餐打卡"] or hn["keywords"][:1] == ["词11"], str(hn))
    pf = aqf.user_performance()
    rep.check("千帆:员工端简版带账号数据和我们稿子的表现", pf["account"]["fans"] == 102 and pf["mine"] and pf["mine"][0]["draft_id"] == did_qf, str(pf)[:200])
    # 多账号:一次回传多个账号各存一份;笔记带账号;按账号看
    db.q_exec("DELETE FROM qf_snapshots WHERE kind IN ('account','notes')")
    qf.ingest({"kind": "account", "account": {"name": "老账号", "fans": 50}})   # 旧版回传:没有账号 ID
    r = qf.ingest({"kind": "account", "accounts": [{"account_id": "u1", "name": "老账号", "fans": 60, "likes": 3}, {"account_id": "u2", "name": "新账号", "fans": "1.2万"}]})
    rep.check("千帆多账号:一次回传 2 个账号各存一份", r["stored"] == 2 and q("SELECT COUNT(*) c FROM qf_snapshots WHERE kind='account'")[0]["c"] == 3, str(r))
    accs = qf.accounts()
    rep.check("千帆多账号:账号列表按粉丝排序,\"1.2万\"换算", [(a["account_id"], a["fans"]) for a in accs] == [("u2", 12000), ("u1", 60)], str(accs)[:200])
    rep.check("千帆多账号:没带 ID 的旧快照按昵称认领给同名账号,趋势不断档", [x["fans"] for x in qf.series(10, "u1")] == [50, 60] and not any(a["account_id"] == "" for a in accs))
    rep.check("千帆多账号:accounts 为空拒绝", _raises2(lambda: qf.ingest({"kind": "account", "accounts": []})))
    qf.ingest({"kind": "notes", "items": [{"title": "空气炸锅早餐!!10分钟搞定", "likes": 30, "account_id": "u1", "account_name": "老账号"},
                                          {"title": "别家笔记甲", "likes": 5, "account_id": "u2", "account_name": "新账号"}, {"title": "没归属的笔记", "likes": 1}]})
    ov_all, ov_u1 = aqf.overview(), aqf.overview(account="u1")
    rep.check("千帆多账号:全部账号看到 3 篇,按账号统计篇数", ov_all["notes"]["total"] == 3 and ov_all["note_counts"] == {"u1": 1, "u2": 1, "": 1}, str(ov_all["note_counts"]))
    rep.check("千帆多账号:选一个账号只看它的笔记和趋势", ov_u1["notes"]["total"] == 1 and ov_u1["notes"]["matched"] == 1 and len(ov_u1["series"]) == 2 and ov_u1["account"]["name"] == "老账号", str(ov_u1["notes"])[:200])
    rep.check("千帆多账号:账号 ID 不存在就回到全部账号", aqf.overview(account="nope")["selected"] == "")
    pf = aqf.user_performance()
    rep.check("千帆多账号:员工端稿子表现带发布账号,主账号取粉丝最多的", pf["account"]["account_id"] == "u2" and pf["mine"][0]["account_name"] == "老账号" and len(pf["accounts"]) == 2, str(pf)[:200])

    for _k in ('a', 'aqf', 'i', 'qf', 'r', 't', 'x',):
        if _k in locals():
            setattr(env, _k, locals()[_k])
