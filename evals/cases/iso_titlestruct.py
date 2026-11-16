"""标题结构:大模型给标题分类(校验 / 只存一次 / 失败不影响)、按结构在真实数据里校准、证据够强才写进提示词、我们稿子的结构对比。分类用假的函数,不调大模型。"""
import datetime


def run(rep, env):
    from app import db, llm, loop, titlelab as tl

    print("\n[标题结构]")
    # ---- llm.tag_titles:校验 ----
    o_chat = llm.chat
    llm.chat = lambda system, user, temperature=1.0: {"items": [
        {"id": 0, "struct": "悬念好奇", "audience": True, "specific": 1, "benefit": 0, "gap": True, "keyword": "x", "fluff": False},
        {"id": 1, "struct": "乱写的类型", "fluff": True},
        {"id": "x", "struct": "数字清单"}, {"struct": "避坑挑选"}, {"id": 3, "struct": "数字清单"}]}
    try:
        got = llm.tag_titles([{"id": 0, "title": "a"}, {"id": 1, "title": "b"}, {"id": 3, "title": "c"}])
        empty = llm.tag_titles([])
    finally:
        llm.chat = o_chat
    rep.check("分类校验:类型不在列表里的算「其他」、特征转成 true/false、缺字段按 false、编号不对 / 缺编号的丢掉;空输入不调模型",
              got[0] == {"struct": "悬念好奇", "audience": True, "specific": True, "benefit": False, "gap": True, "keyword": True, "fluff": False} and got[1]["struct"] == "其他" and got[1]["fluff"] is True
              and got[1]["audience"] is False and set(got) == {0, 1, 3} and empty == {}, str(got))
    rep.check("分类提示词:数字清单要有明确数字 / 序数组织;拿不准归「其他」;输出格式含全部特征", "「一包」" in llm.TITLE_TAG_SYSTEM and "拿不准的归「其他」" in llm.TITLE_TAG_SYSTEM and all(k in llm.TITLE_TAG_SYSTEM for k in llm.TITLE_FEATS))

    # ---- 分类只存一次、失败不影响 ----
    calls = []

    def fake(items):
        calls.append(len(items))
        return {it["id"]: {"struct": "数字清单" if "3" in it["title"] else "其他", **{k: False for k in llm.TITLE_FEATS}, "specific": "3" in it["title"]} for it in items}
    titles = ["3步做好腐乳拌饭", "腐乳拌饭", "豆腐乳拌饭", "  ", "另一个标题"]   # 「腐乳拌饭」和「豆腐乳拌饭」归一化后是同一个标题
    un = tl.untagged(titles, {})
    rep.check("待分类:去掉空标题、归一化后相同的只算一个(豆腐乳 = 腐乳)", len(un) == 3, str(un))
    n1 = tl.tag_now(titles, batch=2, fn=fake)
    tm = tl.tag_map()
    n2 = tl.tag_now(titles, batch=2, fn=fake)
    rep.check("分类落库:按批调用、分类结果存下来(结构 + 特征);已分类的不再重复调用;重复执行不新增",
              n1 == 3 and calls == [2, 1] and n2 == 0 and calls == [2, 1] and tm[tl.title_key("3步做好腐乳拌饭")]["struct"] == "数字清单" and tm[tl.title_key("3步做好腐乳拌饭")]["specific"] is True
              and tl.untagged(titles) == [], str((n1, calls)))
    part = tl.tag_now(["新标题甲", "新标题乙3"], batch=10, fn=lambda items: {0: {"struct": "其他", **{k: False for k in llm.TITLE_FEATS}}})   # 模型只返回了一个:另一个下次再分
    rep.check("模型漏返回的标题不存,留到下次;返回的存下", part == 1 and len(tl.untagged(["新标题甲", "新标题乙3"])) == 1)

    # ---- 后台分类的触发条件 ----
    o_tag = tl.tag_now
    started = []
    tl.tag_now = lambda titles, batch=30, fn=None: started.append(len(titles)) or 0
    try:
        db.save_settings({"deepseek_api_key": ""})
        tl._tag_state.update(running=False, last=0.0)
        a = tl.maybe_start_tagging(["全新标题一", "全新标题二"], sync=True)
        db.save_settings({"deepseek_api_key": "k"})
        tl._tag_state.update(running=False, last=0.0)
        b = tl.maybe_start_tagging(["全新标题一", "全新标题二"], sync=True)
        c = tl.maybe_start_tagging(["全新标题一", "全新标题二"], sync=True)            # 刚试过:间隔内不重复
        tl._tag_state.update(running=False, last=0.0)
        d = tl.maybe_start_tagging(["3步做好腐乳拌饭"], sync=True)                      # 都分类过了:不开始
        tl._tag_state.update(running=True, last=0.0)
        e = tl.maybe_start_tagging(["全新标题三"], sync=True)                            # 正在跑:不重复开
    finally:
        tl.tag_now = o_tag
        tl._tag_state.update(running=False, last=0.0)
        db.save_settings({"deepseek_api_key": ""})
    rep.check("后台分类触发:没配大模型 Key / 间隔内 / 都已分类 / 正在跑 都不开始;其余才开始", (a, b, c, d, e) == (False, True, False, False, False) and started == [2], str((a, b, c, d, e, started)))

    # ---- 按结构校准:悬念好奇的明显更好,只靠空泛夸词的更差,样本少的不下结论 ----
    day = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
    items, tags = [], {}
    for i in range(240):
        struct = "悬念好奇" if i % 4 == 0 else "其他"
        fluff = i % 3 == 0
        title = f"结构测试笔记{i}号"
        likes = 5 + (8 if struct == "悬念好奇" else 0) - (3 if fluff else 0) + (i * 7 % 4)
        items.append({"note_id": f"s{i}", "title": title, "likes": likes, "collects": 1, "comments": 0, "views": 100, "account_id": "accS", "published_at": day})
        tags[tl.title_key(title)] = {"struct": struct, **{k: False for k in llm.TITLE_FEATS}, "fluff": fluff}
    items.append({"note_id": "x", "title": "没分类的笔记", "likes": 99, "views": 10, "account_id": "accS", "published_at": day})
    cal = tl.calibrate_struct(items, tags, 3)
    rows = {r["key"]: r for r in cal["rows"]}
    rep.check("结构校准:只用已分类的标题;悬念好奇明显更好(中位高、排名概率 > 0.6),只靠空泛夸词的更差;没出现过的结构样本太少不下结论;整体按差别排序",
              cal["n_used"] == 240 and rows["悬念好奇"]["verdict"] == "sig_pos" and rows["悬念好奇"]["auc"] > 0.6 and rows["悬念好奇"]["med_with"] > rows["悬念好奇"]["med_without"]
              and rows["fluff"]["verdict"] in ("sig_neg", "lean_neg") and rows["数字清单"]["verdict"] == "few" and rows["has_struct"]["n_with"] == 60
              and [r["verdict"] == "few" for r in cal["rows"]] == sorted(r["verdict"] == "few" for r in cal["rows"]), str({k: (r["verdict"], r["z"]) for k, r in rows.items()}))

    # ---- 提示:证据够强才给 ----
    hint = tl.struct_hint(cal)
    rep.check("标题结构提示:明显更好的结构写进提示(带示例说明),明显更差的「靠空泛夸词」写进去;证据弱的(倾向 / 样本少)不写;没有强证据返回空",
              "悬念" in hint and "空泛夸词" in hint and "结构要有变化" in hint and str(cal["n_used"]) in hint and tl.struct_hint(None) == ""
              and tl.struct_hint({"n_used": 9, "rows": [{"group": "struct", "key": "悬念好奇", "verdict": "lean_pos", "n_with": 90, "n_without": 90}]}) == ""
              and tl.struct_hint({"n_used": 9, "rows": [{"group": "struct", "key": "悬念好奇", "verdict": "sig_pos", "n_with": 10, "n_without": 90}]}) == "", hint)

    # ---- 我们稿子的结构对比 + 总览 ----
    def fx(i, lift, title):
        return {"id": i, "tier": "good", "lift": lift, "days": 5, "title": title, "edit": {"level": "原样", "title_sim": 1.0, "cover": False, "body": False}, "feat": {"score": 80, "dims": {}}}
    tg2 = {tl.title_key("甲标题"): {"struct": "悬念好奇"}, tl.title_key("乙标题"): {"struct": "其他"}}
    ev = {"cfg": {"min_days": 3}, "title_tags": tg2, "rows": [fx(1, 2.0, "甲标题"), fx(2, 1.5, "甲标题"), fx(3, 0.5, "乙标题"), fx(4, 0.7, "乙标题"), fx(5, 3.0, "没分类的标题")]}
    fac = {f["name"]: {x["label"]: x for x in f["items"]} for f in loop.learning(ev)["factors"]}
    rep.check("我们稿子的做法对比里有「标题结构」:按大模型分类的结构分组(没分类的、标题被改过的不算)", fac["标题结构(大模型分类)"]["悬念好奇"]["n"] == 2 and fac["标题结构(大模型分类)"]["悬念好奇"]["lift"] == 1.75
              and fac["标题结构(大模型分类)"]["其他"]["n"] == 2 and "没分类的标题" not in str(fac))
    ov = loop.overview(loop.evaluate(register=False))
    ts = ov["title_struct"]
    rep.check("复盘总览带标题结构:校准结果、我们稿子的结构分布、分类进度", ts is not None and "rows" in ts and "ours" in ts and "running" in ts["tagging"] and "n_titles" in ts, str(list(ts or {})))
    # rule_for 现在给的是结构提示(缓存里有就用)
    import time
    tl._hint_cache.update(at=time.time(), txt="STRUCT-HINT")
    rep.check("给稿子的标题提示来自结构分析(对照组 / 总开关关 / 没有强证据都是空)", tl.rule_for(1, True) == "\n\nSTRUCT-HINT" and tl.rule_for(5, True) == "" and tl.rule_for(1, False) == "")
    tl._hint_cache.update(at=0.0, txt="")
    dflt = next(v for k, v in vars(db).items() if isinstance(v, dict) and "auto_fix" in v)
    rep.check("标题结构提示总开关默认开(证据不够强时不加任何内容)", dflt["title_hint"] == "1")
