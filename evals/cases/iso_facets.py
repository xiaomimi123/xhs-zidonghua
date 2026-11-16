"""笔记库筛选:联动筛选(每个下拉框的选项和数量按其他已选条件统计;已选的值始终在列表里;员工端和管理端用同一套)。"""


def run(rep, env):
    from app import api_admin as aa, api_user, db

    print("\n[笔记库联动筛选]")
    db.q_exec("DELETE FROM notes WHERE uid LIKE 'fx_%'")
    rows = [("fx_1", "fx美食", "fx咖啡", "fx好物种草", "fx产品软文", 5000), ("fx_2", "fx美食", "fx咖啡", "fx好物种草", "fx教程", 10), ("fx_3", "fx美食", "fx早餐", "fx教程", "fx教程", 10),
            ("fx_4", "fx穿搭", "fx通勤", "fx好物种草", "fx产品软文", 10), ("fx_5", "fx穿搭", "fx通勤", "fx测评", "fx测评", 10), ("fx_6", "fx穿搭", "fx秋冬", "fx测评", "fx测评", 10)]
    for uid, track, topic, ait, kind, likes in rows:
        db.q_exec("INSERT INTO notes(uid,title,track,topic,ai_type,ai_status,kind,kind_status,likes) VALUES(?,?,?,?,?,'done',?,'done',?)", uid, uid, track, topic, ait, kind, likes)
    try:
        cnt = lambda lst: {x["k"]: x["n"] for x in lst if str(x["k"]).startswith("fx")}
        base = aa.note_facets()
        rep.check("没选任何条件:各下拉框是全库的数量(这几篇测试笔记:美食 3 / 穿搭 3)", cnt(base["tracks"]) == {"fx美食": 3, "fx穿搭": 3} and cnt(base["types"])["fx好物种草"] == 3, str(cnt(base["tracks"])))
        f1 = aa.note_facets(ai_type="fx好物种草")
        rep.check("选了内容类型「好物种草」:赛道只统计好物种草里的(美食 2 / 穿搭 1),主题里没有的(早餐 / 秋冬)不再列出,帖子类型也只算它里面的;内容类型自己仍列出全部类型",
                  cnt(f1["tracks"]) == {"fx美食": 2, "fx穿搭": 1} and "fx早餐" not in cnt(f1["topics"]) and "fx秋冬" not in cnt(f1["topics"]) and cnt(f1["topics"]) == {"fx咖啡": 2, "fx通勤": 1}
                  and cnt(f1["kinds"]) == {"fx产品软文": 2, "fx教程": 1} and cnt(f1["types"]) == {"fx好物种草": 3, "fx教程": 1, "fx测评": 2}, str((cnt(f1["tracks"]), cnt(f1["topics"]), cnt(f1["kinds"]), cnt(f1["types"]))))
        f2 = aa.note_facets(track="fx穿搭", ai_type="fx好物种草")
        rep.check("再选赛道「穿搭」:主题只剩通勤(1);内容类型按「穿搭」统计(好物种草 1 / 测评 2);赛道列表按「好物种草」统计,选中的穿搭仍在",
                  cnt(f2["topics"]) == {"fx通勤": 1} and cnt(f2["types"]) == {"fx好物种草": 1, "fx测评": 2} and cnt(f2["tracks"]) == {"fx美食": 2, "fx穿搭": 1}, str((cnt(f2["topics"]), cnt(f2["types"]))))
        f3 = aa.note_facets(track="fx美食", ai_type="fx测评")
        rep.check("选了没有结果的组合(美食 + 测评):已选的值仍然留在列表里(数量 0),下拉框不会变成空白、看起来像「全部」却还在筛选",
                  cnt(f3["types"]).get("fx测评") == 0 and any(x["k"] == "fx美食" for x in f3["tracks"]) and next(x for x in f3["tracks"] if x["k"] == "fx美食")["n"] == 0, str(cnt(f3["types"])))
        f4 = aa.note_facets(tier="viral")
        rep.check("分级也联动:只看爆款(likes 5000)时,赛道只剩美食(1)", cnt(f4["tracks"]) == {"fx美食": 1} and cnt(f4["topics"]) == {"fx咖啡": 1}, str((cnt(f4["tracks"]), cnt(f4["topics"]))))
        # 员工端接口:同样联动,且去掉空值
        u = api_user.notebase_facets(track="fx美食", ai_type="fx好物种草")
        rep.check("员工端笔记库筛选项:和管理端一致(美食 + 好物种草:主题咖啡 2),没有空值项", cnt(u["topics"]) == {"fx咖啡": 2} and all(x["k"] for k in u for x in u[k]), str(cnt(u["topics"])))
        # 结果数 = 下拉框里显示的数量
        for kw, want in ((dict(track="fx美食", ai_type="fx好物种草"), 2), (dict(topic="fx通勤"), 2), (dict(ai_type="fx测评", track="fx穿搭"), 2)):
            got = aa.all_notes(**kw, q="fx_")["total"] if False else aa.all_notes(q="fx_", **kw)["total"]
            rep.check(f"下拉框显示的数量和点进去的结果数一致:{kw}", got == want, str(got))
    finally:
        db.q_exec("DELETE FROM notes WHERE uid LIKE 'fx_%'")
