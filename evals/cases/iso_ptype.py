"""帖子类型分类(商业硬文 / 产品软文 / 教程 / 测评 / 其他):规则线索、模型结果校验、后台批处理、笔记库筛选、重新分类。"""
from ..common import _raises2


def run(rep, env):
    from app import api_admin as aa, db, llm, ptype, worker as w4
    user = env.user

    print("\n[帖子类型]")
    f1 = ptype.features("下饭酱测评", "买了5款对比,口感打分,优点缺点都写了 vs 老干妈")
    f2 = ptype.features("配方", "食材:牛奶200ml,咖啡2g\n步骤:第一步加热60度\n1. 倒入杯中\n2. 搅拌")
    f3 = ptype.features("新品上市", "旗舰店下单到手价39元,净含量500g,厂家直销,支持批发定制")
    f4 = ptype.features("我的体验", "我自己买了回购三次,亲测真的好喝,感觉很不错")
    rep.check("规则线索:对比词 / 步骤用量词 / 价格购买词 / 第一人称体验词都能数出来", f1["compare"] >= 4 and f2["steps"] >= 4 and f3["price"] >= 4 and f4["exp"] >= 5, str((f1, f2, f3, f4)))
    rep.check("分类提示词里有三条收紧规则:和产品无关一律归其他、有配方步骤的算教程、同品牌多款算多款对比", "一律归「其他」" in ptype.KIND_SYSTEM and "优先级规则" in ptype.KIND_SYSTEM and "哪怕是同一个品牌的多款" in ptype.KIND_SYSTEM)
    rep.check("五个类型、子类的取值", ptype.KINDS == ["商业硬文", "产品软文", "教程", "测评", "其他"] and ptype.SUBS["测评"] == ["单款", "多款对比"] and "经验科普" in ptype.SUBS["产品软文"])

    # 模型结果校验:类型不在清单里 → 其他;子类不属于这个类型 → 空;把握限制在 0~1;缺 id 的跳过
    orig_chat = llm.chat
    llm.chat = lambda system, user_, temperature=1.0: {"items": [
        {"id": 1, "kind": "测评", "sub": "多款对比", "related": True, "conf": 0.9, "why": "多款红黑榜"},
        {"id": 2, "kind": "乱写的类型", "sub": "单款", "related": False, "conf": 3, "why": "x" * 100},
        {"id": 3, "kind": "教程", "sub": "单款", "conf": -1},
        {"kind": "测评"}, {"id": "abc", "kind": "教程"}]}
    try:
        got = ptype.classify([{"id": 1, "title": "t", "content": "c"}, {"id": 2, "title": "t", "content": "c"}, {"id": 3, "title": "t", "content": "c"}])
        prompt_seen = []
        llm.chat = lambda system, user_, temperature=1.0: (prompt_seen.append(user_), {"items": []})[1]
        ptype.classify([{"id": 9, "title": "咖啡测评", "content": "对比三款"}])
    finally:
        llm.chat = orig_chat
    rep.check("模型结果校验:未知类型归「其他」、子类不匹配清空、把握限制 0~1、why 截断、缺 id 的跳过", got[1] == {"kind": "测评", "sub": "多款对比", "related": True, "conf": 0.9, "why": "多款红黑榜"}
              and got[2]["kind"] == "其他" and got[2]["sub"] == "" and got[2]["related"] is False and got[2]["conf"] == 1.0 and len(got[2]["why"]) == 60
              and got[3]["kind"] == "教程" and got[3]["sub"] == "" and got[3]["conf"] == 0.0 and set(got) == {1, 2, 3}, str(got))
    rep.check("给模型的内容带上规则线索(价格词 / 步骤词 / 对比词 / 体验词个数)", "线索:价格购买词" in prompt_seen[0] and "对比词" in prompt_seen[0] and "【9】" in prompt_seen[0])

    # 后台批处理
    keep = {k: db.get_settings()[k] for k in ("auto_tag", "deepseek_api_key")}
    db.save_settings({"auto_tag": "1", "deepseek_api_key": "k"})
    db.q_exec("DELETE FROM notes WHERE uid LIKE 'pt_%'")
    ids = [db.q_exec("INSERT INTO notes(uid,url,title,content,ai_status,kind_status) VALUES(?,?,?,?,'done','pending')", f"pt_{i}", f"pt_{i}", t, "正文" * 20).lastrowid
           for i, t in enumerate(["咖啡做法教程", "一款咖啡测评", "旅行游记", "品牌新品上市"])]
    orig_cls = w4.ptype.classify
    w4.ptype.classify = lambda rows: {ids[0]: {"kind": "教程", "sub": "配方", "related": True, "conf": 0.9, "why": "有步骤用量"},
                                      ids[1]: {"kind": "测评", "sub": "单款", "related": True, "conf": 0.8, "why": "有评价维度"},
                                      ids[2]: {"kind": "其他", "sub": "", "related": False, "conf": 0.95, "why": "旅行"}}
    try:
        db.q_exec("UPDATE notes SET kind_status='done' WHERE uid NOT LIKE 'pt_%'")   # 别的用例留下的笔记不参与
        more = w4._kind_batch()
        rows = {r["uid"]: r for r in db.q_all("SELECT * FROM notes WHERE uid LIKE 'pt_%'")}
        rep.check("后台分类:一批处理完,返回 True 表示还可以继续", more is True and rows["pt_0"]["kind"] == "教程" and rows["pt_0"]["kind_sub"] == "配方" and rows["pt_0"]["kind_status"] == "done" and rows["pt_0"]["kind_conf"] == 0.9)
        rep.check("后台分类:和产品无关的记 kind_rel=0;模型没返回的那篇标成出错", rows["pt_2"]["kind_rel"] == 0 and rows["pt_3"]["kind_status"] == "error", str({k: (v["kind_status"], v["kind_rel"]) for k, v in rows.items()}))
        rep.check("后台分类:没有待办时返回 False", w4._kind_batch() is False)
        w4.ptype.classify = lambda rows: (_ for _ in ()).throw(RuntimeError("余额不足"))
        db.q_exec("UPDATE notes SET kind_status='pending' WHERE uid='pt_3'")
        stop = w4._kind_batch()
        r3 = db.q_one("SELECT kind_status, kind_why FROM notes WHERE uid='pt_3'")
        rep.check("后台分类:接口出错就停(不空转),记下原因", stop is False and r3["kind_status"] == "error" and "余额不足" in r3["kind_why"], str(r3))
        db.save_settings({"deepseek_api_key": ""})
        db.q_exec("UPDATE notes SET kind_status='pending' WHERE uid='pt_3'")
        rep.check("没配 Key / 关了自动打标签时不动", w4._kind_batch() is False and db.q_one("SELECT kind_status FROM notes WHERE uid='pt_3'")["kind_status"] == "pending")
        db.save_settings({"deepseek_api_key": "k"})

        # 笔记库:按帖子类型筛选 / 筛选项 / 字段
        nb = user.get("/api/notebase?kind=教程").json()
        mine = [x for x in nb["items"] if x["id"] == ids[0]]
        rep.check("员工端笔记库:按帖子类型筛选,带类型和子类,和产品无关的带标记", mine and mine[0]["kind"] == "教程" and mine[0]["kind_sub"] == "配方" and all(x["kind"] == "教程" for x in nb["items"])
                  and next(x for x in user.get("/api/notebase?kind=其他").json()["items"] if x["id"] == ids[2])["kind_rel"] == 0)
        fa = user.get("/api/notebase/facets").json()
        rep.check("笔记库筛选项里有帖子类型(含数量)", {"教程", "测评", "其他"} <= {x["k"] for x in fa["kinds"]}, str(fa["kinds"]))
        # 重新分类
        db.q_exec("UPDATE notes SET kind_status='error' WHERE uid='pt_3'")
        r_failed = aa.rekind(aa.RekindIn(scope="failed"))
        rep.check("重新分类:只重试出错的", r_failed["n"] >= 1 and db.q_one("SELECT kind_status FROM notes WHERE uid='pt_3'")["kind_status"] == "pending" and db.q_one("SELECT kind_status FROM notes WHERE uid='pt_0'")["kind_status"] == "done")
        r_one = aa.rekind(aa.RekindIn(scope="教程"))
        rep.check("重新分类:指定某个类型的全部重来", r_one["n"] >= 1 and db.q_one("SELECT kind_status FROM notes WHERE uid='pt_0'")["kind_status"] == "pending")
        r_all = aa.rekind(aa.RekindIn(scope="all"))
        rep.check("重新分类:全部重来", r_all["n"] >= 4 and db.q_one("SELECT COUNT(*) n FROM notes WHERE kind_status!='pending'")["n"] == 0)
    finally:
        w4.ptype.classify = orig_cls
        db.save_settings(keep)
        db.q_exec("DELETE FROM notes WHERE uid LIKE 'pt_%'")

    # ---------- 复刻时按类型用 ----------
    from app import autorun as au
    rep.check("类型比例:只保留已知类型和大于 0 的份数", ptype.parse_mix({"产品软文": 6, "测评": "4", "其他": 3, "教程": 0, "乱写": 2, "商业硬文": "x"}) == {"产品软文": 6, "测评": 4})
    sc = ptype.split_count(10, {"产品软文": 6, "教程": 3, "测评": 2})
    rep.check("按比例分篇数:总数不变、每类至少 1 篇", sum(sc.values()) == 10 and min(sc.values()) >= 1 and sc["产品软文"] > sc["教程"] >= sc["测评"], str(sc))
    rep.check("篇数少于类型数时按权重取最大的几类", sum(ptype.split_count(2, {"产品软文": 6, "教程": 3, "测评": 2}).values()) == 2 and ptype.split_count(0, {"教程": 1}) == {})
    rep.check("每个类型都有写作要求,且互相区分", set(ptype.RULES) == {"产品软文", "教程", "测评", "商业硬文"} and "步骤" in ptype.RULES["教程"] and "评价维度" in ptype.RULES["测评"] and "第一人称" in ptype.RULES["产品软文"] and "禁止出现" in ptype.RULES["产品软文"] and "具体用量" in ptype.RULES["产品软文"])
    base = {"kind_status": "done", "kind": "教程", "kind_rel": 1, "kind_conf": 0.8, "content": "正文" * 30}
    rep.check("能当参考的条件:已分类、类型明确、和产品相关、正文够长、把握不低", ptype.usable(base)
              and not ptype.usable({**base, "kind_status": "pending"}) and not ptype.usable({**base, "kind": "其他"}) and not ptype.usable({**base, "kind_rel": 0})
              and not ptype.usable({**base, "content": "短"}) and not ptype.usable({**base, "kind_conf": 0.3}))

    pj = db.q_exec("INSERT INTO projects(track,keywords) VALUES('美食','[\"咖啡\"]')").lastrowid
    nids = []
    for i, (k, c) in enumerate([("教程", 0.9), ("教程", 0.8), ("教程", 0.7), ("测评", 0.9), ("产品软文", 0.9), ("产品软文", 0.8), ("产品软文", 0.7), ("商业硬文", 0.9)]):
        nid = db.q_exec("INSERT INTO notes(uid,url,title,content,likes,note_type,keyword,kind,kind_status,kind_conf,kind_rel,ai_status) VALUES(?,?,?,?,?,'image','咖啡',?, 'done', ?, 1,'done')",
                        f"pk_{i}", f"pk_{i}", f"咖啡做法{i}", "咖啡做法正文" * 10, 100 - i, k, c).lastrowid
        db.q_exec("INSERT INTO project_notes(project_id,note_id) VALUES(?,?)", pj, nid)
        nids.append(nid)
    try:
        g = dict(au.pick_by_kind(pj, db.get_settings(), {"产品软文": 6, "测评": 4, "教程": 2}))
        rep.check("按类型挑参考:每类只用本类的笔记", set(g["产品软文"]) == set(nids[4:7]) and set(g["教程"]) == set(nids[0:3]), str(g))
        rep.check("某一类可用笔记太少(只有 1 篇测评)就跳过这一类", "测评" not in g)
        rep.check("一个类型都凑不出就返回空(调用方退回按主题)", au.pick_by_kind(pj, db.get_settings(), {"商业硬文": 1}) == [])
    finally:
        db.q_exec("DELETE FROM project_notes WHERE project_id=?", pj)
        db.q_exec("DELETE FROM notes WHERE uid LIKE 'pk_%'")
        db.q_exec("DELETE FROM projects WHERE id=?", pj)

    # 写完后校验类型:不一致重写一次,记录实际类型
    calls, o_w, o_chk = [], llm.write_note, ptype.check
    pj2 = db.q_exec("INSERT INTO projects(track,keywords) VALUES('美食','[]')").lastrowid
    did = db.q_exec("INSERT INTO drafts(project_id,ptype) VALUES(?,?)", pj2, "教程").lastrowid
    try:
        llm.write_note = lambda project, pb, refs, angle, extra="", kb="", banned="": (calls.append(extra), {"title": "重写版", "content": "x"})[1]
        seq = iter(["产品软文", "教程"])
        ptype.check = lambda title, content: next(seq)
        out = w4._match_ptype({}, {"kind": "教程", "data": {}}, [], {"id": did, "angle": "", "extra": ""}, {"title": "旧", "content": "y"}, "TAIL", "", "")
        rep.check("类型不一致:带反馈重写一次,实际类型记在稿子上", out["title"] == "重写版" and len(calls) == 1 and "被判成了「产品软文」" in calls[0] and calls[0].endswith("TAIL")
                  and db.q_one("SELECT ptype_got FROM drafts WHERE id=?", did)["ptype_got"] == "教程")
        ptype.check = lambda title, content: "教程"
        calls.clear()
        out = w4._match_ptype({}, {"kind": "教程", "data": {}}, [], {"id": did, "angle": "", "extra": ""}, {"title": "旧", "content": "y"}, "", "", "")
        rep.check("类型一致:不重写", out["title"] == "旧" and not calls)
        ptype.check = lambda title, content: (_ for _ in ()).throw(RuntimeError("网络"))
        out = w4._match_ptype({}, {"kind": "教程", "data": {}}, [], {"id": did, "angle": "", "extra": ""}, {"title": "旧", "content": "y"}, "", "", "")
        rep.check("判类型出错:原稿照用,不影响出稿", out["title"] == "旧")
        db.q_exec("UPDATE drafts SET title='类型标签', content='x', status='ready', ptype='教程', ptype_got='产品软文' WHERE id=?", did)
        item = next((x for x in user.get("/api/library").json() if x["id"] == did), None)
        rep.check("成品库返回稿子的目标类型和实际类型(预览页据此显示标签)", item and item["ptype"] == "教程" and item["ptype_got"] == "产品软文", str(item and (item["ptype"], item["ptype_got"])))
        rep.check("拆解卡没有类型(旧流程):完全不介入", w4._match_ptype({}, {"kind": "", "data": {}}, [], {"id": did}, {"title": "旧"}, "", "", "")["title"] == "旧")
    finally:
        llm.write_note, ptype.check = o_w, o_chk
        db.q_exec("DELETE FROM drafts WHERE id=?", did)
        db.q_exec("DELETE FROM projects WHERE id=?", pj2)
