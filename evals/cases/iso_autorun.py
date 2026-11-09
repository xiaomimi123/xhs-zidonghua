"""一键批量生成的主流程。"""
import json
from app import db


def run(rep, env):
    q = env.q
    print("\n[一键批量生成]")
    import datetime as _dt11
    from app import autorun as au, worker as wk11
    from app.notes import save_notes as sn11
    rep.check("宽泛词判断", au.is_broad("咖啡") and au.is_broad("腐乳") and not au.is_broad("腐乳做法") and not au.is_broad("手冲咖啡教程"))
    oks = au.kw_suggest
    au.kw_suggest = lambda track, w: {"broad": True, "suggestions": [{"word": w + "做法"}, {"word": w + "怎么吃"}, {"word": w + "测评"}]}
    try:
        rep.check("关键词:词少时一个宽泛词换两个", au.refine_keywords("美食", ["腐乳"]) == ["腐乳做法", "腐乳怎么吃"])
        rep.check("关键词:词多时每个换一个,最多 5 个", au.refine_keywords("美食", ["腐乳", "咖啡", "豆浆", "面包", "牛奶", "燕麦"]) == ["腐乳做法", "咖啡做法", "豆浆做法", "面包做法", "牛奶做法"])
        rep.check("关键词:已经具体的不动", au.refine_keywords("美食", ["腐乳做法"]) == ["腐乳做法"])
        rep.check("关键词:没填就取赛道预设词", len(au.refine_keywords("美食", [], 2)) >= 2)
    finally:
        au.kw_suggest = oks
    rep.check("篇数分配:余数给靠前的组", au.allocate(10, 3) == [4, 3, 3] and au.allocate(2, 3) == [1, 1, 0] and au.allocate(5, 0) == [])
    S11 = {"auto_pass_score": "75", "auto_review": "1"}
    D11 = dict(id=1, status="ready", review={"overall": 90}, checks={}, images=["a.png"], image_prompts=["p"], with_images=1, title="t", error="")
    rep.check("质检:合格", au.judge(D11, S11) == (True, "", ""))
    rep.check("质检:分数不够 → 重写", au.judge({**D11, "review": {"overall": 60}}, S11)[2] == "redo")
    rep.check("质检:高风险词 → 重写", au.judge({**D11, "checks": {"high": 1}}, S11)[2] == "redo")
    rep.check("质检:选题冲突 → 重写", au.judge({**D11, "checks": {"combo": [["a", "b"]]}}, S11)[2] == "redo")
    rep.check("质检:配图不全 → 补图", au.judge({**D11, "images": ["a.png", ""], "image_prompts": ["p", "q"]}, S11)[2] == "img")
    rep.check("质检:出错且缺图 → 补图", au.judge({**D11, "status": "error", "images": [""]}, S11)[2] == "img")
    rep.check("质检:生成失败 → 重写", au.judge({**D11, "status": "error", "title": "", "images": []}, S11)[2] == "redo")
    rep.check("质检:有待补充内容 → 需检查,不重试", au.judge({**D11, "checks": {"placeholders": [{"text": "价格"}]}}, S11)[0] is False and au.judge({**D11, "checks": {"placeholders": [{"text": "价格"}]}}, S11)[2] == "")
    rep.check("完成判断:没审完不算完成", not au.finished({**D11, "review": {}}, S11) and au.finished(D11, S11) and au.finished({**D11, "status": "error"}, S11))
    rep.check("完成判断:没有配图提示词的稿子审完就是终态", au.finished({**D11, "status": "text_done", "image_prompts": []}, S11))

    today11 = _dt11.date.today().isoformat()
    db.save_settings({"auto_tag": "0", "auto_min_notes": "3", "auto_min_relevant": "5", "auto_refs": "3", "auto_images": "0", "auto_review": "1", "auto_retry": "1", "auto_pass_score": "75"})
    pid11 = db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('美食','[\"腐乳做法\"]','')").lastrowid
    sn11([{"note_id": f"AU{i}", "title": f"腐乳做法{i}", "content": "腐乳做法" * 5, "likes": 100 * (i + 1), "published_at": today11} for i in range(6)],
         project_id=pid11, keyword="腐乳做法", track="美食")
    db.q_exec("INSERT INTO crawl_tasks(project_id,type,query,status) VALUES(?,?,?,?)", pid11, "keyword", "腐乳做法", "running")
    au.new_run(pid11, 4)
    o_sp, o_pas, o_st, o_si = wk11.submit_playbook, wk11.plan_and_submit, wk11.submit_text, wk11.submit_images
    redone = []
    wk11.submit_playbook = lambda pb_id: db.q_exec("UPDATE playbooks SET status='done', data='{}' WHERE id=?", pb_id)
    def fake_plan(project_id, playbook_id, count, extra, with_images):
        return [db.q_exec("INSERT INTO drafts(project_id,playbook_id,status,with_images) VALUES(?,?,'queued',0)", project_id, playbook_id).lastrowid for _ in range(count)]
    wk11.plan_and_submit = fake_plan
    wk11.submit_text = lambda did: (redone.append(did), db.q_exec("UPDATE drafts SET status='queued' WHERE id=?", did))
    wk11.submit_images = lambda did, redo_all=False: None
    run = lambda: q("SELECT * FROM autoruns WHERE project_id=?", pid11)[0]
    try:
        au.tick()
        rep.check("批量:采集任务没完成时继续等待", run()["status"] == "collecting", run()["status"])
        db.q_exec("UPDATE crawl_tasks SET status='done' WHERE project_id=?", pid11)
        au.tick()
        rep.check("批量:采集完成 → 分类", run()["status"] == "tagging", run()["status"])
        au.tick()
        r1 = run()
        rep.check("批量:自动建拆解卡并进入拆解", r1["status"] == "analyzing" and len(r1["state"]["pbs"]) >= 1, str(r1["state"]))
        au.tick()
        r2 = run(); dids = r2["state"]["drafts"]
        rep.check("批量:按目标篇数建稿", r2["status"] == "writing" and len(dids) == 4, f"{r2['status']} {len(dids)}")
        rep.check("批量:进度接口", au.progress(pid11)["drafts"]["total"] == 4 and au.progress(pid11)["active"])
        for i, did in enumerate(dids):  # 3 篇合格,1 篇分数太低
            db.q_exec("UPDATE drafts SET status='text_done', title='t', review=?, checks='{}' WHERE id=?", json.dumps({"overall": 50 if i == 0 else 90}), did)
        au.tick()
        rep.check("批量:不合格的自动重写一次", run()["status"] == "retrying" and redone == [dids[0]], f"{run()['status']} {redone}")
        db.q_exec("UPDATE drafts SET status='text_done', review=? WHERE id=?", json.dumps({"overall": 55}), dids[0])
        au.tick()
        r3 = run(); pg = au.progress(pid11)
        rep.check("批量:重写后仍不合格 → 标成需检查,不再重写", r3["status"] == "done" and redone == [dids[0]] and pg["drafts"]["passed"] == 3 and pg["drafts"]["failed"] == 1, str(pg))
        bad = q("SELECT auto_ok, auto_note FROM drafts WHERE id=?", dids[0])[0]
        rep.check("批量:需检查的稿子写明原因", bad["auto_ok"] == 0 and "55" in bad["auto_note"], str(bad))
        au.cancel(pid11)
        rep.check("批量:完成后取消不改变状态", run()["status"] == "done")
        # 采到的笔记太少:停下并说明原因
        pid12 = db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('美食','[\"x\"]','')").lastrowid
        db.q_exec("INSERT INTO crawl_tasks(project_id,type,query,status) VALUES(?,?,?,?)", pid12, "keyword", "x", "done")
        au.new_run(pid12, 3); au.tick()
        r4 = q("SELECT * FROM autoruns WHERE project_id=?", pid12)[0]
        rep.check("批量:笔记太少时停下并提示", r4["status"] == "error" and "至少需要" in r4["msg"], r4["msg"])
        # 取消
        pid13 = db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('美食','[\"y\"]','')").lastrowid
        tid13 = db.q_exec("INSERT INTO crawl_tasks(project_id,type,query,status) VALUES(?,?,?,?)", pid13, "keyword", "y", "pending").lastrowid
        au.new_run(pid13, 3); au.cancel(pid13)
        rep.check("批量:取消后停止并撤掉待领取的采集任务", q("SELECT status FROM autoruns WHERE project_id=?", pid13)[0]["status"] == "canceled" and q("SELECT status FROM crawl_tasks WHERE id=?", tid13)[0]["status"] == "failed")
    finally:
        wk11.submit_playbook, wk11.plan_and_submit, wk11.submit_text, wk11.submit_images = o_sp, o_pas, o_st, o_si

    for _k in ('_', 'au', 'extra', 'i', 'pid11', 'pid13', 'r4', 'sn11', 'today11', 'w', 'wk11',):
        if _k in locals():
            setattr(env, _k, locals()[_k])
