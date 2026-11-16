"""复盘页四步(发出去 → 数据回来 → 谁好谁差 → 下一步)要用的数字:近 7 天发布、对上率、各档数量、分组对比、给下一批的建议;等级名称;「有数据的」筛选。"""
import datetime


def run(rep, env):
    from app import api_user, loop

    print("\n[复盘四步]")
    rep.check("等级名称用大白话:爆款 / 表现好 / 一般 / 偏低 / 数据还在涨 / 没绑定笔记 / 未发布",
              [loop.TIERS[k] for k in ("winner", "good", "normal", "weak", "observing", "nodata", "unpublished")] == ["爆款", "表现好", "一般", "偏低", "数据还在涨", "没绑定笔记", "未发布"]
              and loop.WITH_DATA == ("winner", "good", "normal", "weak", "observing"))
    today = datetime.date.today()
    day = lambda n: (today - datetime.timedelta(days=n)).isoformat()

    def row(i, tier, lift, views, pub_day, cover="实拍图 + 排字", acc="甲账号", ptype="产品软文", linked=True, published=True):
        return {"id": i, "tier": tier, "lift": lift, "views": views, "published": published, "published_at": pub_day, "account_name": acc, "cover_name": cover, "ptype": ptype, "topic": "吃法",
                "edit": {"level": "原样"} if linked else None, "days": 5}
    rows = [row(1, "good", 1.5, 100, day(0)), row(2, "normal", 1.0, 40, day(0)), row(3, "weak", 0.5, 10, day(2)), row(4, "observing", 0.4, 5, day(1)),
            row(5, "nodata", None, None, day(3), linked=False), row(6, "nodata", None, None, day(9), linked=False), row(7, "winner", 3.0, 500, day(8)),
            {"id": 8, "tier": "unpublished", "lift": None, "views": None, "published": False, "published_at": "", "edit": None}]
    ev = {"rows": rows, "cfg": {"min_days": 3}, "data_at": "2026-11-16 01:00"}
    S = loop.review_steps(ev)
    rep.check("第 1 步:已发布总数(未发布的不算);近 7 天每天发布数(今天 2 篇、昨天 1 篇、2 天前 1 篇、3 天前 1 篇);本周 5 篇 / 上周 2 篇",
              S["pub"]["total"] == 7 and S["pub"]["days"][-1]["n"] == 2 and S["pub"]["days"][-2]["n"] == 1 and S["pub"]["week"] == 5 and S["pub"]["last_week"] == 2 and len(S["pub"]["days"]) == 7, str(S["pub"]))
    rep.check("第 2 步:对上率 = 已绑定 / 已发布;还在涨的篇数;带数据时间和同步间隔",
              S["data"]["linked"] == 5 and S["data"]["published"] == 7 and S["data"]["rate"] == 0.71 and S["data"]["waiting"] == 1 and S["data"]["data_at"] == "2026-11-16 01:00" and S["data"]["hours"] >= 1)
    rep.check("第 3 步:表现好(爆款 + 表现好)/ 一般 / 偏低 的篇数,有数据的总数、发布满 N 天的篇数",
              S["result"]["good"] == 2 and S["result"]["normal"] == 1 and S["result"]["low"] == 1 and S["result"]["n"] == 5 and S["result"]["n_mature"] == 4, str(S["result"]))
    cmp_ = {g["name"]: g for g in S["compare"]["cover"]}
    rep.check("分组对比:按封面 / 账号 / 类型 / 选题分组,篇数、平均阅读、发布满 N 天的中位倍数;只有已对上数据的稿子参与", cmp_["实拍图 + 排字"]["n"] == 5 and cmp_["实拍图 + 排字"]["views_avg"] == 131 and cmp_["实拍图 + 排字"]["lift"] == 1.25
              and set(S["compare"]) == {"account", "cover", "ptype", "topic"} and S["compare"]["account"][0]["name"] == "甲账号")
    rep.check("建议:有结论的稿子少于 10 篇不给建议,只说还差几篇", S["next"]["advice"] == [] and S["next"]["need"] == 6 and S["next"]["winners"] == 1)

    # 数据够了:某个封面明显更好、某个明显偏低 → 给建议(每组至少 5 篇)
    many = []
    for i in range(6):
        many.append(row(100 + i, "good", 1.6, 80, day(5), cover="实拍图 + 排字"))
    for i in range(6):
        many.append(row(200 + i, "weak", 0.5, 10, day(5), cover="程序排版(纯文字)"))
    S2 = loop.review_steps({"rows": many, "cfg": {"min_days": 3}, "data_at": ""})
    adv = " ".join(S2["next"]["advice"])
    rep.check("建议:有结论的稿子 ≥ 10 篇、某类每组 ≥ 5 篇且差距够大时才给(写明篇数和倍数);偏高的说多用,偏低的说少用或换写法;最多 3 条",
              "封面「实拍图 + 排字」" in adv and "多用" in adv and "封面「程序排版(纯文字)」" in adv and "少用" in adv and len(S2["next"]["advice"]) <= 3 and S2["next"]["need"] == 0, adv)
    few = [row(300 + i, "good", 1.6, 80, day(5), cover="甲封面") for i in range(4)] + [row(400 + i, "weak", 0.5, 10, day(5), cover="乙封面") for i in range(4)] + [row(500 + i, "normal", 1.0, 30, day(5), cover="丙封面") for i in range(4)]
    rep.check("建议:每组不足 5 篇不给(样本少)", loop.review_steps({"rows": few, "cfg": {"min_days": 3}, "data_at": ""})["next"]["advice"] == [])

    # 「有数据的」筛选
    ev2 = {"rows": [{"tier": t, "lift": 1, "id": i, "account_id": "a"} for i, t in enumerate(["winner", "good", "normal", "weak", "observing", "nodata", "unpublished"])], "cfg": {}}
    got = {r["tier"] for r in api_user._loop_rows(ev2, "withdata")}
    rep.check("「有数据的」筛选 = 爆款 / 表现好 / 一般 / 偏低 / 数据还在涨,不含没绑定和未发布;空 = 全部;指定某档只出那档",
              got == {"winner", "good", "normal", "weak", "observing"} and len(api_user._loop_rows(ev2, "")) == 7 and {r["tier"] for r in api_user._loop_rows(ev2, "weak")} == {"weak"})
    ov = loop.overview(loop.evaluate(register=False))
    rep.check("复盘总览带四步数据", "steps" in ov and {"pub", "data", "result", "next", "compare"} <= set(ov["steps"]))
