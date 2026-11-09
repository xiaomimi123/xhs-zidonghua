"""看图结果解析、重新运行、关键词层次、提前结束、候选关键词、整理主题。"""
import json
from ..common import _raises2
from app import db


def run(rep, env):
    Sync, _o_adj26, aa16, adm, au, au13, o_dir12, pid11, pj1, ppid, pr12, q, w4 = env.Sync, env._o_adj26, env.aa16, env.adm, env.au, env.au13, env.o_dir12, env.pid11, env.pj1, env.ppid, env.pr12, env.q, env.w4
    # 看图结果解析 / 重试 / 按产品名兜底
    from app import llm as llm23
    okj = llm23._json_in('```json\n{"category": "金汤酱料", "keywords": ["金汤酱料做法", "金汤酱料推荐"],}\n```')
    rep.check("看图解析:去掉 ``` 围栏和多余的逗号", okj["category"] == "金汤酱料" and okj["keywords"] == ["金汤酱料做法", "金汤酱料推荐"], str(okj))
    loose = llm23._json_in('```json\n{\n "category": "复合调味料",\n "track": "美食",\n "appearance": "黑红配色包装袋,印有"一酱成菜"字样",\n "keywords": [\n "金汤酱料做法",\n "金汤酱料测评"')
    rep.check("看图解析:JSON 坏了或被截断时,把品类和关键词抠出来", loose["category"] == "复合调味料" and loose["keywords"] == ["金汤酱料做法", "金汤酱料测评"], str(loose))
    rep.check("看图解析:什么有用的都没有才报错", _raises2(lambda: llm23._json_in("抱歉,我看不清这张图")) and _raises2(lambda: llm23._json_in('{"foo": ')))
    ov23, calls23 = llm23.vision_chat, []
    def fake_v23(system, text, images, model=None, s=None):
        calls23.append(1)
        return "我不太确定" if len(calls23) == 1 else '{"category": "金汤酱料", "track": "美食", "keywords": ["金汤酱料做法", "金汤酱料推荐", "金汤酱料测评", "金汤酱料食谱", "金汤酱料怎么用", "金汤酱料怎么选", "多余的第七个词"]}'
    llm23.vision_chat = fake_v23
    try:
        d23 = llm23.describe_product("呷么呷么金汤酱料", [], ["美食"])
    finally:
        llm23.vision_chat = ov23
    rep.check("看图:第一次格式不对会再问一次,搜索词最多 6 个", len(calls23) == 2 and d23["category"] == "金汤酱料" and len(d23["keywords"]) == 6, str(d23))
    oc23 = llm23.chat
    llm23.chat = lambda system, user, temperature=1.0: {"category": "金汤酱料", "track": "美食", "keywords": ["金汤酱料做法", "金汤酱料测评", "x"]}
    try:
        bn23 = llm23.describe_by_name("呷么呷么金汤酱料", ["美食"])
    finally:
        llm23.chat = oc23
    rep.check("按产品名判断品类:给出品类、赛道、不带品牌的搜索词", bn23["category"] == "金汤酱料" and bn23["track"] == "美食" and bn23["keywords"] == ["金汤酱料做法", "金汤酱料测评"], str(bn23))
    od23, ob23, opool23, oks23 = llm23.describe_product, llm23.describe_by_name, w4._pool, au.kw_suggest
    w4._pool = lambda kind: Sync()
    au.kw_suggest = lambda track, w: {"broad": False, "suggestions": []}
    llm23.describe_product = lambda name, images, tracks: (_ for _ in ()).throw(RuntimeError("看图失败"))
    llm23.describe_by_name = lambda name, tracks: {"category": "金汤酱料", "track": "美食", "keywords": ["金汤酱料做法", "金汤酱料测评"], "by_name": True}
    try:
        pj23 = db.q_exec("INSERT INTO projects(track,keywords,direction,product_id) VALUES('','[]','',?)", ppid).lastrowid
        au.new_run(pj23, 3, "profiling"); au.tick()
        r23 = q("SELECT track, keywords FROM projects WHERE id=?", pj23)[0]
        rep.check("看图失败:按产品名判断品类,搜索词不再是品牌名", r23["track"] == "美食" and r23["keywords"] == ["金汤酱料做法", "金汤酱料测评"] and "按产品名判断" in q("SELECT msg FROM autoruns WHERE project_id=?", pj23)[0]["msg"], str(r23))
    finally:
        llm23.describe_product, llm23.describe_by_name, w4._pool, au.kw_suggest = od23, ob23, opool23, oks23
    # 重新运行
    rep.check("重新运行:进行中的任务不能重跑", _raises2(lambda: au13.rerun(pj1)))  # pj1:产品任务,还在采集
    db.q_exec("UPDATE autoruns SET status='error' WHERE project_id=?", pj1)
    rr24 = au13.rerun(pj1)
    rrp24 = q("SELECT * FROM projects WHERE id=?", rr24["id"])[0]
    rra24 = q("SELECT status, target FROM autoruns WHERE project_id=?", rr24["id"])[0]
    rep.check("重新运行产品任务:新建任务、重新识别产品图,原来的不动", rr24["id"] != pj1 and rrp24["product_id"] == q("SELECT product_id FROM projects WHERE id=?", pj1)[0]["product_id"] and rra24["status"] == "profiling" and rra24["target"] == 3
              and q("SELECT status FROM autoruns WHERE project_id=?", pj1)[0]["status"] == "error", str(rra24))
    rt24 = au13.rerun(pid11)  # pid11:赛道任务,已完成
    rtp24 = q("SELECT * FROM projects WHERE id=?", rt24["id"])[0]
    rep.check("重新运行赛道任务:沿用关键词和篇数", rt24["id"] != pid11 and rtp24["track"] == "美食" and rtp24["keywords"] == ["腐乳做法"] and q("SELECT target FROM autoruns WHERE project_id=?", rt24["id"])[0]["target"] == 4, str(rtp24))
    au.cancel(rt24["id"]); au.cancel(rr24["id"])
    # 关键词有层次 / 提前结束
    from app import llm as llm26
    llm26.expand_adjacent = _o_adj26  # 前面补充搜索词的测试里把它换成了空实现,这里还原
    rep.check("关键词去重:同一个核心词最多留 2 个", au.diversify(["金汤酱料推荐", "金汤酱料做法", "金汤酱料测评", "酸菜鱼做法"], 2) == ["金汤酱料推荐", "金汤酱料做法", "酸菜鱼做法"])
    prof26 = {"category": "金汤酱料", "keywords": ["金汤酱料推荐", "金汤酱料做法", "金汤酱料测评", "下饭小菜做法"], "broader": ["复合调味料", "火锅底料"], "uses": ["酸菜鱼", "肥牛", "一人食", "多余的"]}
    bk26 = au.build_keywords(prof26, "呷么呷么金汤酱料", "美食", 8)
    rep.check("产品搜索词分层:2 个核心词 + 最多 3 个菜品/用法 + 1 个上位品类,不带品牌", bk26 == ["金汤酱料推荐", "金汤酱料做法", "酸菜鱼做法", "肥牛做法", "一人食做法", "复合调味料做法"], str(bk26))
    rep.check("非食品的菜品词补「推荐」不补「做法」", au.build_keywords({"category": "防晒霜", "keywords": ["防晒霜怎么选"], "uses": ["军训"]}, "x", "美妆护肤")[1] == "军训推荐")
    rep.check("没有分层信息时退回核心词", au.build_keywords({"category": "红腐乳", "keywords": []}, "x", "美食") == ["红腐乳做法", "红腐乳怎么选"])
    ov26 = llm26.vision_chat
    llm26.vision_chat = lambda system, text, images, model=None, s=None: '{"category": "金汤酱料", "keywords": ["金汤酱料做法"], "broader": ["复合调味料", "火锅底料", "第三个"], "uses": ["酸菜鱼", "肥牛"]}'
    try:
        d26 = llm26.describe_product("x", [], ["美食"])
    finally:
        llm26.vision_chat = ov26
    rep.check("看图结果带上位品类和菜品用法", d26["broader"] == ["复合调味料", "火锅底料"] and d26["uses"] == ["酸菜鱼", "肥牛"], str(d26))
    oc26 = llm26.chat
    llm26.chat = lambda system, user, temperature=1.0: {"keywords": ["复合调味料推荐", "酸菜鱼做法", "已有的词", "酸菜鱼做法", "x"]}
    try:
        adj26 = llm26.expand_adjacent("金汤酱料", "美食", 10, ["已有的词"])
    finally:
        llm26.chat = oc26
    rep.check("相邻关键词:去重、去掉已有的、太短的(不要求带品类词)", adj26 == ["复合调味料推荐", "酸菜鱼做法"], str(adj26))
    pdt26 = db.q_exec("INSERT INTO products(name,category,profile,images) VALUES('测试酱','金汤酱料',?,'[]')", json.dumps({"category": "金汤酱料", "uses": ["酸菜鱼", "肥牛"], "broader": ["火锅底料"]})).lastrowid
    pj26 = db.q_exec("INSERT INTO projects(track,keywords,direction,product_id) VALUES('美食',?,'',?)", json.dumps(["金汤酱料做法", "酸菜鱼做法"]), pdt26).lastrowid
    tu26 = au.topup_keywords(q("SELECT * FROM projects WHERE id=?", pj26)[0])
    rep.check("补充搜索词:优先用产品资料里还没搜过的菜品/上位品类", "肥牛做法" in tu26 and "酸菜鱼做法" not in tu26 and len(tu26) <= 3, str(tu26))
    # 相关笔记已经够多:还没采的任务不用等了
    pj27 = db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('美食','[\"榴莲酥做法\"]','')").lastrowid
    db.save_settings({"auto_library_min": "999", "auto_enough_relevant": "10", "auto_tag": "0", "auto_min_notes": "3"})
    au.source(pj27, ["榴莲酥做法"], db.get_settings())
    au.new_run(pj27, 3, "collecting")
    au.tick()
    rep.check("相关笔记够多:还没采的关键词任务提前结束,直接往下走", q("SELECT status FROM autoruns WHERE project_id=?", pj27)[0]["status"] == "tagging" and q("SELECT COUNT(*) c FROM crawl_tasks WHERE project_id=? AND status='pending'", pj27)[0]["c"] == 0, q("SELECT status, msg FROM autoruns WHERE project_id=?", pj27)[0]["msg"])
    rep.check("提前结束的任务写明原因", any("足够多" in (t["error"] or "") for t in q("SELECT error FROM crawl_tasks WHERE project_id=?", pj27)))
    db.save_settings({"auto_enough_relevant": "40"})
    au.cancel(pj27)
    # 载入候选关键词
    ct27 = aa16.library_candidates(source="track", track="美食")
    rep.check("载入赛道预设词:已在队列里的会去掉", len(ct27["keywords"]) + ct27["skipped"] >= 5 and all(isinstance(k, str) for k in ct27["keywords"]), str(ct27)[:120])
    db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('美食',?,'')", json.dumps(["候选词甲", "候选词乙"]))
    cp27 = aa16.library_candidates(source="projects")
    rep.check("载入用户做过的关键词:去重,只载入不添加", {"候选词甲", "候选词乙"} <= set(cp27["keywords"]) and len(cp27["keywords"]) == len(set(cp27["keywords"])) and q("SELECT COUNT(*) c FROM crawl_tasks WHERE query='候选词甲'")[0]["c"] == 0)
    rep.check("载入赛道预设词:没选赛道拒绝", _raises2(lambda: aa16.library_candidates(source="track", track="不存在的赛道")))
    pr12.delete(ppid)
    rep.check("删除产品:图片文件一起删除", pr12.get(ppid) is None and not list(pr12.DIR.iterdir()))
    pr12.DIR = o_dir12

    print("\n[整理主题]")
    from fastapi import HTTPException as _HE
    db.q_exec("UPDATE notes SET topic='珠海咖啡店' WHERE uid='T1'")
    db.q_exec("UPDATE notes SET topic='厦门咖啡厅' WHERE uid='T2'")
    tl = {x["k"]: x["n"] for x in adm.topics()["items"]}
    rep.check("主题列表带篇数", tl.get("珠海咖啡店") == 1 and tl.get("厦门咖啡厅") == 1, str(tl))
    res = adm.merge_topics(adm.TopicMerge(names=["珠海咖啡店", "厦门咖啡厅"], to="咖啡店探店"))
    rep.check("勾选几个主题合并成一个", res["n"] == 2 and {r["topic"] for r in q("SELECT topic FROM notes WHERE uid IN ('T1','T2')")} == {"咖啡店探店"}, str(res))
    adm.merge_topics(adm.TopicMerge(names=["咖啡店探店"], to="咖啡探店"))
    rep.check("只勾一个就是改名", {r["topic"] for r in q("SELECT topic FROM notes WHERE uid IN ('T1','T2')")} == {"咖啡探店"})
    try:
        adm.merge_topics(adm.TopicMerge(names=["咖啡探店"], to="  "))
        rep.check("没填新名称时拒绝", False)
    except _HE as ex:
        rep.check("没填新名称时拒绝", ex.status_code == 400)

    for _k in ('k', 'r', 't', 'x',):
        if _k in locals():
            setattr(env, _k, locals()[_k])
