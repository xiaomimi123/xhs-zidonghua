"""标题写法校准(titlelab):排名检验、个别爆款不影响结论、校准出明显规律、标题写法分、复盘总览带校准结果。纯计算,不碰网络。"""
import datetime


def run(rep, env):
    from app import loop, titlelab as tl

    print("\n[标题写法校准]")
    # ---- 排名检验 ----
    auc, z = tl.mwu([5, 6, 7, 8], [1, 2, 3, 4])
    rep.check("排名检验:一组全部比另一组大,AUC = 1、z 为正;相反方向 AUC = 0;完全相同 AUC = 0.5、z = 0",
              auc == 1.0 and z > 2 and tl.mwu([1, 2, 3, 4], [5, 6, 7, 8])[0] == 0.0 and tl.mwu([3, 3, 3], [3, 3, 3]) == (0.5, 0.0), str((auc, z)))
    a = [0.9] * 20 + [1000.0]       # 一个极端爆款 + 一堆和对照一样的
    b = [0.9] * 21
    auc2, z2 = tl.mwu(a, b)
    rep.check("排名检验:个别极端爆款不会造出显著差别(平均数会被它拉到几十倍,排名几乎没变)", abs(z2) < 2 and 0.45 < auc2 < 0.56 and sum(a) / len(a) > 40, str((auc2, z2)))
    rep.check("结论分级:|z| ≥ 2.5 明显,2 ~ 2.5 有倾向,< 2 看不出;任一边不足 15 篇样本太少",
              tl.verdict(0.6, 2.6, 30, 30) == "sig_pos" and tl.verdict(0.4, -2.6, 30, 30) == "sig_neg" and tl.verdict(0.57, 2.2, 30, 30) == "lean_pos"
              and tl.verdict(0.43, -2.2, 30, 30) == "lean_neg" and tl.verdict(0.52, 0.8, 30, 30) == "none" and tl.verdict(0.9, 5, 14, 200) == "few")

    # ---- 用造出来的笔记校准:带「｜」的明显更好,疑问词的更差,价格样本太少 ----
    day = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
    items = []
    for i in range(160):
        sep, q = i % 4 == 0, i % 5 == 0
        title = ("标题甲｜" if sep else "标题甲") + ("怎么吃" if q else "直接吃") + str(i)
        likes = 4 + (8 if sep else 0) + (-3 if q else 0) + (i * 7 % 5)
        items.append({"note_id": f"n{i}", "title": title, "likes": likes, "collects": 1, "comments": 0, "views": 100 + likes * 10, "account_id": "acc1", "published_at": day})
    items.append({"note_id": "young", "title": "太新的笔记｜怎么吃", "likes": 999, "collects": 0, "comments": 0, "views": 9999, "account_id": "acc1", "published_at": datetime.date.today().isoformat()})
    items.append({"note_id": "nodate", "title": "没日期", "likes": 5, "views": 50, "account_id": "acc1", "published_at": ""})
    cal = tl.calibrate(items, 3)
    rows = {r["key"]: r for r in cal["rows"]}
    rep.check("校准:只用发布满 3 天、有日期的笔记(太新的、没日期的不算)", cal["n_notes"] == 162 and cal["n_used"] == 160, str((cal["n_notes"], cal["n_used"])))
    rep.check("校准:带「｜」的明显更好(AUC > 0.5、z 为正),含疑问词的更差(z 为负),没出现过价格的样本太少不下结论",
              rows["sep"]["verdict"] == "sig_pos" and rows["sep"]["auc"] > 0.6 and rows["question"]["verdict"] in ("sig_neg", "lean_neg") and rows["price"]["verdict"] == "few"
              and rows["sep"]["n_with"] == 40 and rows["sep"]["med_with"] > rows["sep"]["med_without"], str({k: (r["verdict"], r["z"]) for k, r in rows.items() if k in ("sep", "question", "price")}))
    rep.check("校准:结果按差别从明显到不明显排,样本太少的排在最后", [r["verdict"] == "few" for r in cal["rows"]] == sorted(r["verdict"] == "few" for r in cal["rows"])
              and abs(cal["rows"][0]["z"]) >= abs(cal["rows"][1]["z"]))

    # ---- 标题写法分 ----
    good = tl.title_score("标题甲｜直接吃", cal)
    bad = tl.title_score("标题甲怎么吃?", cal)
    rep.check("标题写法分:带「｜」不带疑问词的高于不带「｜」带疑问词的;分数在 5~95;命中的写法带加减分;没有校准结果时给空",
              good["score"] > 50 > bad["score"] and 5 <= bad["score"] and good["score"] <= 95 and any(h["name"] == "标题带「｜」「【」" and h["has"] and h["delta"] > 0 for h in good["hits"])
              and tl.title_score("随便", None) == {"score": None, "hits": []}, str((good, bad)))

    # ---- 缓存、特征、总览 ----
    rep.check("标题特征:数字 / 问号 / 称呼读者 / 夸张钩子 / 价格 能认出来",
              tl.title_features("3个方法,姐妹谁懂?12.8元")["digit"] and tl.title_features("3个方法,姐妹谁懂?12.8元")["mark"] and tl.title_features("姐妹们")["you"]
              and tl.title_features("谁懂啊")["hook"] and tl.title_features("12.8元好物")["price"] and not tl.title_features("普通标题")["digit"])
    c1 = tl.cached(items, "d1", 3)
    rep.check("校准缓存:同一份数据同一天只算一次", tl.cached(items, "d1", 3) is c1 and tl.cached(items, "d2", 3) is not c1)
    ev = loop.evaluate(register=False)
    ov = loop.overview(ev)
    rep.check("复盘总览带标题校准(含我们已生成的稿子里各写法占比)和标题写法分的验证",
              "title_lab" in ov and (ov["title_lab"] is None or ("ours" in ov["title_lab"] and ov["title_lab"]["ours"]["n"] >= 0)) and "title_check" in ov["learn"], str(list(ov)[:12]))
    # 验证:写法分和真实表现的相关,样本不足 8 篇不给
    ev_fake = {"cfg": {"min_days": 3}, "title_cal": cal, "rows": [{"id": i, "tier": "good", "lift": 1 + i * 0.1, "days": 5, "title": "标题甲｜直接吃" if i % 2 else "标题甲怎么吃?", "feat": {"score": 80, "dims": {}}} for i in range(10)]}
    tc = loop.learning(ev_fake)["title_check"]
    rep.check("标题写法分的验证:有 8 篇以上有结论的稿子才给相关系数;不够只给篇数", tc["n"] == 10 and tc["corr"] is not None and loop.learning({**ev_fake, "rows": ev_fake["rows"][:5]})["title_check"]["corr"] is None, str(tc))

    # ---- 标题写法提示:只用「明显」且样本够的写法;对照组;写稿时真的带上、并记下 ----
    import json
    import time
    from app import db, llm, worker
    big = {"n_used": 500, "rows": [
        {"key": "emoji", "name": "e", "verdict": "sig_pos", "n_with": 100, "n_without": 400, "auc": 0.6, "z": 3.0},
        {"key": "question", "name": "q", "verdict": "sig_neg", "n_with": 80, "n_without": 420, "auc": 0.4, "z": -3.0},
        {"key": "sep", "name": "s", "verdict": "lean_pos", "n_with": 90, "n_without": 410, "auc": 0.56, "z": 2.2},        # 只是倾向:不进提示
        {"key": "digit", "name": "d", "verdict": "sig_pos", "n_with": 10, "n_without": 490, "auc": 0.7, "z": 3.1},         # 一边只有 10 篇:不进提示
        {"key": "me", "name": "m", "verdict": "none", "n_with": 90, "n_without": 410, "auc": 0.5, "z": 0.1}]}
    h = tl.prompt_hint(big)
    rep.check("提示词:只用「明显」且两边样本都 ≥ 30 的写法(倾向的、样本少的、没差别的都不进);有利的说「可以」,不利的说「不要」;提醒句式要有变化",
              "emoji" in h and tl.ADVICE["emoji"][0] in h and tl.ADVICE["question"][1] in h and tl.ADVICE["sep"][0] not in h and tl.ADVICE["digit"][0] not in h
              and "句式要有变化" in h and "500 篇" in h and tl.prompt_hint(None) == "" and tl.prompt_hint({"n_used": 5, "rows": []}) == "", h)
    rep.check("对照组:稿子编号能被 5 整除的不给提示(每 5 篇 1 篇),其余给", [tl.is_control(i) for i in (1, 2, 3, 4, 5, 10)] == [False, False, False, False, True, True])
    old_cache = dict(tl._hint_cache)
    tl._hint_cache.update(at=time.time(), txt="HINT")
    try:
        rep.check("按稿子给提示:缓存里有就带上;对照组、总开关关了都是空;没有明显规律(空提示)也是空",
                  tl.rule_for(1, True) == "\n\nHINT" and tl.rule_for(5, True) == "" and tl.rule_for(1, False) == "" and (tl._hint_cache.update(txt="") or tl.rule_for(1, True) == ""))
    finally:
        tl._hint_cache.update(old_cache)

    # 写稿时真的带上提示:非对照组带、对照组不带,并记在 checks.title_hint 里
    pj = db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('美食','[\"腐乳做法\"]','')").lastrowid
    pb = db.q_exec("INSERT INTO playbooks(project_id,name,note_ids,data,status) VALUES(?,?,?,?,'done')", pj, "t", "[]", "{}").lastrowid
    ids = []
    while len({i % 5 == 0 for i in ids}) < 2:
        ids.append(db.q_exec("INSERT INTO drafts(project_id,playbook_id,angle,extra,status,with_images) VALUES(?,?,?,?,'queued',0)", pj, pb, "角度", "").lastrowid)
    treated = next(i for i in ids if i % 5 != 0)
    control = next(i for i in ids if i % 5 == 0)
    got = {}
    o_wn, o_sub = llm.write_note, worker.submit_text
    llm.write_note = lambda project, playbook, refs, angle, extra="", kb="", banned="": got.__setitem__(len(got), extra) or {"title": "t", "content": "正文", "alt_titles": [], "tags": ["a"], "image_prompts": []}
    worker.submit_text = lambda did: None
    db.save_settings({"auto_review": "0", "auto_fix": "0", "auto_compress": "0", "title_hint": "1"})
    tl._hint_cache.update(at=time.time(), txt="HINT-TEXT")
    try:
        worker._run_text(treated)
        worker._run_text(control)
        db.save_settings({"title_hint": "0"})
        later = max(ids) + 1 if (max(ids) + 1) % 5 != 0 else max(ids) + 2
        off = db.q_exec("INSERT INTO drafts(id,project_id,playbook_id,angle,extra,status,with_images) VALUES(?,?,?,?,?,'queued',0)", later, pj, pb, "角度", "").lastrowid
        worker._run_text(off)
    finally:
        llm.write_note, worker.submit_text = o_wn, o_sub
        tl._hint_cache.update(old_cache)
        db.save_settings({"title_hint": "1"})
    ck = {i: db.q_one("SELECT checks FROM drafts WHERE id=?", i)["checks"] for i in (treated, control, off)}
    rep.check("写稿:非对照组的提示词里带标题写法参考,对照组和总开关关闭时不带;checks.title_hint 记 1 / 0 / 0",
              "HINT-TEXT" in got[0] and "HINT-TEXT" not in got[1] and "HINT-TEXT" not in got[2]
              and ck[treated]["title_hint"] == 1 and ck[control]["title_hint"] == 0 and ck[off]["title_hint"] == 0, str((got, ck)))
    rep.check("快照:记下写这篇时有没有带提示;没记录的老稿是 None", loop.snapshot({"title": "x", "checks": {"title_hint": 1}})["title_hint"] == 1 and loop.snapshot({"title": "x", "checks": {}})["title_hint"] is None
              and loop.snapshot({"title": "x"})["title_hint"] is None)

    # 对比:带提示 vs 对照;标题被改过的不算;老稿(None)不算;只有一组时不给
    def fx(i, lift, hint, ts=1.0):
        return {"id": i, "tier": "good", "lift": lift, "days": 5, "title": "t", "edit": {"level": "原样", "title_sim": ts, "cover": False, "body": False},
                "feat": {"score": 80, "dims": {}, "title_hint": hint}}
    ev_h = {"cfg": {"min_days": 3}, "rows": [fx(1, 2.0, 1), fx(2, 1.0, 1), fx(3, 0.5, 0), fx(4, 9.0, 1, ts=0.4), fx(5, 3.0, None)]}
    he = {x["label"]: x for x in loop.learning(ev_h)["hint_effect"]}
    rep.check("提示有没有用:带提示 2 篇(标题被改过的不算)vs 对照 1 篇;老稿不算;只有一组时不给",
              he["带提示"]["n"] == 2 and he["带提示"]["lift"] == 1.5 and he["没带提示(对照)"]["n"] == 1 and loop.learning({**ev_h, "rows": ev_h["rows"][:2]})["hint_effect"] == [])
