"""数据看板总览:总粉丝每日变化(没同步的日子沿用上次、新账号第一次出现前按它第一次的数算、可按店铺)、近 30 天表现最好的笔记、市场热点预览。"""
import datetime


def run(rep, env):
    from app import api_qianfan, db, qianfan

    print("\n[数据看板总览]")
    today = (datetime.datetime.utcnow() + datetime.timedelta(hours=qianfan.TZ_OFFSET_H)).date()
    ago = lambda n: (datetime.datetime.utcnow() - datetime.timedelta(days=n)).strftime("%Y-%m-%d %H:%M:%S")
    A = lambda aid, fans: {"account_id": aid, "name": aid, "fans": fans}
    qianfan.ingest({"kind": "account", "shop_id": "bd1", "shop_name": "看板甲店", "accounts": [A("bda", 100), A("bdb", 50)]})
    db.q_exec("UPDATE qf_snapshots SET snap_at=? WHERE shop_id='bd1' AND kind='account'", ago(3))
    qianfan.ingest({"kind": "account", "shop_id": "bd1", "shop_name": "看板甲店", "accounts": [A("bda", 110), A("bdb", 55)]})
    qianfan.ingest({"kind": "account", "shop_id": "bd2", "shop_name": "看板乙店", "accounts": [A("bdc", 30)]})

    tr = qianfan.fans_trend(30, "bd1")
    byday = {t["day"]: t["fans"] for t in tr}
    rep.check("总粉丝趋势:按店铺算,最近 30 天每天一个点;3 天前同步一次(150)、今天又同步一次(165),中间没同步的日子沿用上一次;第一次同步之前按第一次算(不凭空少)",
              len(tr) == 30 and tr[-1]["day"] == today.isoformat() and byday[today.isoformat()] == 165 and byday[(today - datetime.timedelta(days=1)).isoformat()] == 150
              and byday[(today - datetime.timedelta(days=2)).isoformat()] == 150 and byday[(today - datetime.timedelta(days=20)).isoformat()] == 150, str(tr[-5:]))
    allt = {t["day"]: t["fans"] for t in qianfan.fans_trend(30, "")}
    rep.check("全部店铺:今天 = 各账号最新粉丝之和;新账号(乙店)在它第一次出现之前按它第一次的数算,总数不会在它出现那天凭空跳一截",
              allt[today.isoformat()] >= 195 and allt[(today - datetime.timedelta(days=10)).isoformat()] >= 180 and allt[today.isoformat()] - allt[(today - datetime.timedelta(days=1)).isoformat()] == 15)
    syn = {t["day"]: t["synced"] for t in tr}
    rep.check("趋势点标出哪天真的同步过:今天和 3 天前是真同步,中间沿用上次的日子不是(图上只给同步过的日子画小圆点)",
              syn[today.isoformat()] and syn[(today - datetime.timedelta(days=3)).isoformat()] and not syn[(today - datetime.timedelta(days=1)).isoformat()] and not syn[(today - datetime.timedelta(days=10)).isoformat()])
    rep.check("没有数据的店铺返回空;趋势长度可调", qianfan.fans_trend(30, "没有这个店") == [] and len(qianfan.fans_trend(7, "bd1")) == 7)

    # 笔记 / 热点预览
    notes = [{"note_id": f"bdn{i}", "title": f"看板笔记{i}", "views": 100 + i, "likes": likes, "collects": 1, "comments": 0, "account_id": "bda", "account_name": "bda",
              "published_at": (today - datetime.timedelta(days=d)).isoformat()} for i, (likes, d) in enumerate([(50, 2), (5, 3), (30, 1), (80, 60), (10, 4)])]
    qianfan.ingest({"kind": "notes", "shop_id": "bd1", "shop_name": "看板甲店", "items": notes})
    qianfan.ingest({"kind": "hot_keywords", "shop_id": "bd1", "scope": "", "period": "7d", "items": [{"rank": i + 1, "name": f"词{i}", "keyword": f"词{i}", "heat": 100 - i} for i in range(9)]})
    qianfan.ingest({"kind": "hot_topics", "scope": "", "period": "7d", "items": [{"rank": i + 1, "name": f"话题{i}", "heat": 90 - i} for i in range(9)]})
    o = api_qianfan.overview(shop="bd1")
    sm = o["summary"]
    rep.check("总览汇总:近 30 天表现最好的 3 篇(按互动排,60 天前的那篇 80 赞不算),带标题、账号、阅读、赞",
              [n["title"] for n in sm["top_notes"]] == ["看板笔记0", "看板笔记2", "看板笔记4"] and sm["top_notes"][0]["likes"] == 50 and {"title", "account_name", "views", "likes"} <= set(sm["top_notes"][0]), str(sm["top_notes"]))
    rep.check("总览汇总:总粉丝趋势跟着店铺走;市场热点预览取热门关键词 / 话题各前 6 个,带更新时间和周期",
              len(sm["trend"]) == 90 and sm["trend"][-1]["fans"] == 165 and sm["hot"]["hot_keywords"]["items"] == [f"词{i}" for i in range(6)] and sm["hot"]["hot_topics"]["items"] == [f"话题{i}" for i in range(6)]
              and sm["hot"]["hot_keywords"]["period"] == "7d" and sm["hot"]["hot_keywords"]["at"], str(sm["hot"]))
    rep.check("员工端看板接口同样带总览汇总,并且不带同步设置", "summary" in api_qianfan.board_overview(shop="bd1") and "settings" not in api_qianfan.board_overview(shop="bd1"))
