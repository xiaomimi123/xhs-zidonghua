"""发布账号与选题分析:稿子对应账号、选题归类、按账号推荐。"""
from ..common import _raises2
from app import db


def run(rep, env):
    aqf, q, qf = env.aqf, env.q, env.qf
    # 稿子 ↔ 发布账号:任务指定账号 → 稿子继承;可换可清;按账号对号,不串号;成品库带出表现
    from app import worker as wk_ac, api_user as au_ac
    rep.check("发布账号:账号 ID 必须是千帆里有的", au_ac._pick_account("u1") == ("u1", "老账号") and au_ac._pick_account("") == ("", "") and _raises2(lambda: au_ac._pick_account("nope")))
    pj_ac = db.q_exec("INSERT INTO projects(track,keywords,account_id,account_name) VALUES('美食','[]','u1','老账号')").lastrowid
    class _NoPool:
        def submit(self, *a, **k): pass
    o_pool_ac = wk_ac._pool
    wk_ac._pool = lambda kind: _NoPool()
    try:
        ids_ac = wk_ac.plan_and_submit(pj_ac, 0, 2, "", True)
    finally:
        wk_ac._pool = o_pool_ac
    rep.check("发布账号:任务指定的账号,稿子继承", all(r["account_id"] == "u1" and r["account_name"] == "老账号" for r in q("SELECT account_id, account_name FROM drafts WHERE id IN (?,?)", *ids_ac)))
    au_ac.set_draft_account(ids_ac[1], au_ac.AccountIn(account_id="u2"))
    rep.check("发布账号:单篇可以换账号", q("SELECT account_id, account_name FROM drafts WHERE id=?", ids_ac[1])[0] == {"account_id": "u2", "account_name": "新账号"})
    au_ac.set_draft_account(ids_ac[1], au_ac.AccountIn(account_id=""))
    rep.check("发布账号:清除后回到未指定", q("SELECT account_id FROM drafts WHERE id=?", ids_ac[1])[0]["account_id"] == "")
    rep.check("发布账号:换成不存在的账号拒绝", _raises2(lambda: au_ac.set_draft_account(ids_ac[0], au_ac.AccountIn(account_id="nope"))))
    # 两个账号发了同标题的笔记:各自对回指定的账号的稿子
    db.q_exec("UPDATE drafts SET title='同名标题测试', status='ready' WHERE id=?", ids_ac[0])
    db.q_exec("UPDATE drafts SET title='同名标题测试', status='ready', account_id='u2', account_name='新账号' WHERE id=?", ids_ac[1])
    m_ac = qf.match_drafts([{"title": "同名标题测试", "account_id": "u2", "likes": 9}, {"title": "同名标题测试", "account_id": "u1", "likes": 5}])
    rep.check("发布账号:同标题按账号各对各的,不串号", m_ac.get(ids_ac[1], {}).get("likes") == 9 and m_ac.get(ids_ac[0], {}).get("likes") == 5, str(m_ac))
    qf.ingest({"kind": "notes", "items": [{"title": "同名标题测试", "likes": 5, "views": 100, "account_id": "u1", "account_name": "老账号"}]})
    lib_ac = {d["id"]: d for d in au_ac.library(pj_ac)}
    rep.check("发布账号:成品库带账号,对上号的带发布后表现", lib_ac[ids_ac[0]]["account_name"] == "老账号" and lib_ac[ids_ac[0]]["perf"]["views"] == 100 and lib_ac[ids_ac[1]]["perf"] is None, str({k: v for k, v in lib_ac[ids_ac[0]].items() if k in ("account_name", "perf")}))
    rep.check("发布账号:员工端账号列表", [a["account_id"] for a in aqf.user_accounts()] == ["u2", "u1"])
    # 批量指定账号 / 没指定账号的稿子按对上的笔记补账号
    r_b = au_ac.set_account_batch(au_ac.AccountBatch(ids=[ids_ac[0], ids_ac[1], 99999999], account_id="u2"))
    rep.check("发布账号:批量指定,返回成功个数(不存在的 id 略过)", r_b["n"] == 2 and r_b["account_name"] == "新账号"
              and all(r["account_id"] == "u2" for r in q("SELECT account_id FROM drafts WHERE id IN (?,?)", ids_ac[0], ids_ac[1])))
    rep.check("发布账号:批量指定的账号必须是千帆里有的;没选稿子 400",
              _raises2(lambda: au_ac.set_account_batch(au_ac.AccountBatch(ids=[ids_ac[0]], account_id="nope"))) and _raises2(lambda: au_ac.set_account_batch(au_ac.AccountBatch(ids=[], account_id="u1"))))
    au_ac.set_account_batch(au_ac.AccountBatch(ids=[ids_ac[0]], account_id=""))
    rep.check("发布账号:批量清除回到未指定", q("SELECT account_id FROM drafts WHERE id=?", ids_ac[0])[0]["account_id"] == "")
    from app import loop as loop_ac
    loop_ac.evaluate(register=False)
    rep.check("发布账号:只看不登记时不改稿子的账号", q("SELECT account_id FROM drafts WHERE id=?", ids_ac[0])[0]["account_id"] == "")
    loop_ac.evaluate()
    row_fill = q("SELECT account_id, account_name FROM drafts WHERE id=?", ids_ac[0])[0]
    rep.check("发布账号:没指定账号的稿子,对上的千帆笔记带账号就补上(用笔记的账号);已指定的不动",
              row_fill["account_id"] == "u1" and row_fill["account_name"] == "老账号" and q("SELECT account_id FROM drafts WHERE id=?", ids_ac[1])[0]["account_id"] == "u2", str(row_fill))
    pg_ac = au_ac.loop_page(reg="todo", noacc=0)
    rep.check("复盘页:发布登记带「没指定账号」的篇数,noacc=1 只留没指定的", pg_ac["reg"]["noacc"] == sum(1 for r in au_ac.loop_page(reg="todo", size=100)["reg"]["items"] if not r["account_id"])
              and all(not r["account_id"] for r in au_ac.loop_page(reg="todo", noacc=1, size=100)["reg"]["items"]))
    # 选题分析:笔记按标题归类后,按账号汇总哪类选题表现好
    from app import llm as llm_tp
    db.q_exec("DELETE FROM qf_snapshots WHERE kind='notes'")
    db.q_exec("DELETE FROM qf_note_tags")
    mk = lambda t, likes, acc, views=100: {"note_id": f"tp-{t}-{likes}-{acc}", "title": t, "views": views, "likes": likes, "collects": 0, "comments": 0, "account_id": acc, "account_name": acc}
    qf.ingest({"kind": "notes", "items": [mk("拌饭a", 30, "u1"), mk("拌饭b", 20, "u1"), mk("拌饭c", 10, "u1"), mk("招募a", 4, "u1"), mk("招募b", 3, "u1"), mk("招募c", 3, "u1"), mk("拌饭d", 4, "u2"),
                                          mk("拌饭e", 0, "u3", 100), mk("拌饭f", 0, "u3", 100), mk("拌饭g", 0, "u3", 100), mk("招募d", 0, "u3", 20), mk("招募e", 0, "u3", 20), mk("招募f", 0, "u3", 20)]})
    rep.check("选题分析:没配 DeepSeek Key 时不会自动归类", qf.start_tagging().get("error") and len(qf.untagged()) == 13)
    o_tag, o_ang = llm_tp.tag_notes, llm_tp.angle_notes
    calls = {"tag": 0, "angle": 0}
    def fake_tag(notes, known_topics=None, search_terms=None):
        calls["tag"] += len(notes)
        return {n["id"]: ({"type": "教程攻略", "topic": "拌饭"} if n["title"].startswith("拌饭") else {"type": "其他", "topic": "招募"}) for n in notes}
    def fake_angle(notes):
        calls["angle"] += len(notes)
        return {n["id"]: ("吃法搭配" if n["title"].startswith("拌饭") else "促销活动") for n in notes}
    llm_tp.tag_notes, llm_tp.angle_notes = fake_tag, fake_angle
    try:
        n_tag = qf.tag_notes_now(batch=4)
        n_again = qf.tag_notes_now(batch=4)
    finally:
        llm_tp.tag_notes, llm_tp.angle_notes = o_tag, o_ang
    rep.check("选题分析:分批归类全部笔记,已归类的不重复归类", n_tag == 13 and n_again == 0 and not qf.untagged() and calls == {"tag": 13, "angle": 13}, f"{n_tag} {n_again} {calls}")
    tr = qf.topic_report("u1")
    by = {x["name"]: x for x in tr["by_angle"]}
    rep.check("选题分析:按账号、按选题角度汇总平均互动,跑赢账号平均标「好」,偏低标「弱」", tr["total"] == 6 and tr["metric"] == "eng" and by["吃法搭配"]["n"] == 3 and by["吃法搭配"]["eng_avg"] == 20 and by["吃法搭配"]["lift"] == 1.71 and by["吃法搭配"]["verdict"] == "好" and by["促销活动"]["verdict"] == "弱", str(tr["by_angle"])[:300])
    rep.check("选题分析:按表现从高到低排,带代表笔记和互动率", tr["by_angle"][0]["name"] == "吃法搭配" and tr["by_angle"][0]["best"]["title"] == "拌饭a" and tr["by_angle"][0]["rate"] == 0.2, str(tr["by_angle"][0]))
    t3 = qf.topic_report("u3")
    by3 = {x["name"]: x for x in t3["by_angle"]}
    rep.check("选题分析:账号互动整体太低时改用阅读量比较", t3["metric"] == "views" and by3["吃法搭配"]["verdict"] == "好" and by3["促销活动"]["verdict"] == "弱" and by3["吃法搭配"]["lift"] == 1.67, str(t3["by_angle"])[:300])
    rep.check("选题分析:不选账号看全部,样本少于 3 篇只标样本少", {x["name"]: x["verdict"] for x in qf.topic_report("u2")["by_angle"]} == {"吃法搭配": "样本少"} and qf.topic_report("")["total"] == 13)
    rep.check("选题分析:接口返回同样的结果,还有内容类型和主题", aqf.topics("u1")["base"]["n"] == 6 and aqf.topics("u1")["by_type"] and aqf.topics("u1")["by_topic"])
    # 按账号推荐选题角度 / 搜索词:只推荐系统能如实写的角度;任务带上账号偏好和表现好的标题
    rc = qf.recommend("u1")
    rep.check("按账号推荐:只推荐系统能写的角度里跑赢平均的,依据来自账号数据", rc["recommended"] == ["吃法搭配"] and not rc["note"].startswith("可写的角度里没有") and rc["low_data"], str(rc)[:300])
    rep.check("按账号推荐:账号里表现好的标题只取能写的角度,按表现排序", qf.style_titles("u1", 3) == ["拌饭a", "拌饭b", "拌饭c"], str(qf.style_titles("u1", 3)))
    pj_pref = au_ac._new_project("美食", ["腐乳吃法"], "", with_tasks=False, account=("u1", "老账号"), angles=["吃法搭配", "不存在的角度", "吃法搭配"])
    row_pref = q("SELECT angle_pref, account_style FROM projects WHERE id=?", pj_pref)[0]
    rep.check("按账号推荐:任务存下角度偏好(只认固定的那套)和账号标题", row_pref["angle_pref"] == "吃法搭配" and "拌饭a" in row_pref["account_style"], str(row_pref))
    txt_pref = llm_tp._account_text({"angle_pref": "吃法搭配,产品种草", "account_style": "- 拌饭a"})
    rep.check("按账号推荐:规划和写作的提示里带上角度偏好和标题", "吃法搭配、产品种草" in txt_pref and "拌饭a" in txt_pref and llm_tp._account_text({}) == "")
    qf.ingest({"kind": "notes", "items": [mk("促销x", 30, "u4"), mk("促销y", 30, "u4"), mk("促销z", 30, "u4"), mk("种草a", 1, "u4"), mk("种草b", 1, "u4"), mk("种草c", 1, "u4")]})
    o_tag2, o_ang2 = llm_tp.tag_notes, llm_tp.angle_notes
    llm_tp.tag_notes = lambda notes, known_topics=None, search_terms=None: {n["id"]: {"type": "其他", "topic": "测试"} for n in notes}
    llm_tp.angle_notes = lambda notes: {n["id"]: ("促销活动" if n["title"].startswith("促销") else "产品种草") for n in notes}
    try:
        qf.tag_notes_now(batch=10)
    finally:
        llm_tp.tag_notes, llm_tp.angle_notes = o_tag2, o_ang2
    r4 = qf.recommend("u4")
    rep.check("按账号推荐:数据里最好的是系统不能写的角度(促销)时不推荐它,并说明原因,回到默认", r4["recommended"] == ["产品种草", "吃法搭配"] and "促销活动" in r4["note"] and "不会编造" in r4["note"], str(r4)[:300])
    calls_kw = []
    o_kw = llm_tp.account_keywords
    llm_tp.account_keywords = lambda profile, n=6: calls_kw.append(profile) or ["腐乳吃法", "腐乳拌饭"]
    try:
        k1, k2 = qf.account_keywords("u4"), qf.account_keywords("u4")
    finally:
        llm_tp.account_keywords = o_kw
    rep.check("按账号推荐:搜索词按账号画像生成,同一份数据只问一次 AI", k1 == k2 == ["腐乳吃法", "腐乳拌饭"] and len(calls_kw) == 1 and "种草a" in calls_kw[0], str(calls_kw)[:200])
    rep.check("按账号推荐:接口 账号不存在 404,存在返回推荐", _raises2(lambda: aqf.user_account_plan("nope")) and aqf.user_account_plan("u1")["name"] == "老账号" and aqf.user_account_plan("u1")["recommended"])

    for _k in ('a', 'd', 'r', 't', 'v', 'x',):
        if _k in locals():
            setattr(env, _k, locals()[_k])
