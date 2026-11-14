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
