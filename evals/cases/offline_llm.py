"""不依赖数据库的检查:选题冲突、DeepSeek 重试、笔记主题、字数约束、导入。"""


def run(rep):
    from app.notes import parse_file
    print("\n[选题冲突检查]")
    from app import worker as w3, llm as llm3
    refs3 = [{"title": "姜黄粉食用指南", "content": "姜黄粉加黑胡椒"}, {"title": "速溶咖啡", "content": "冻干咖啡冲泡"}, {"title": "腐乳蘸酱", "content": "广东人的蘸酱"}]
    rt3 = w3._ref_texts(refs3)
    rep.check("选题冲突:检出参考里没有的组合", w3.forced_pairs(["腐乳", "咖啡", "姜黄粉"], "腐乳配咖啡一起吃", rt3) == [("腐乳", "咖啡")])
    rep.check("选题冲突:只提到一个词不算", w3.forced_pairs(["腐乳", "咖啡"], "咖啡的三种冲法", rt3) == [])
    rep.check("选题冲突:参考笔记里本来就同时出现的不算", w3.forced_pairs(["腐乳", "蘸酱"], "腐乳蘸酱怎么调", rt3) == [])
    seq, orig_an = [], llm3.analyze
    def fake_an(*a, feedback="", **k):
        seq.append(feedback)
        return {"name": "腐乳咖啡拆解" if len(seq) <= BAD_N[0] else "饮品写法拆解", "summary": "总结"}
    llm3.analyze = fake_an
    proj3 = {"track": "美食", "keywords": ["腐乳", "咖啡"]}
    try:
        BAD_N = [1]
        d1 = w3._analyze_checked(proj3, refs3, {"track": "美食", "ref": None, "top": None, "normal": None, "comparable": False})
        rep.check("拆解卡名称把词糊在一起:带反馈重做一次", d1["name"] == "饮品写法拆解" and len(seq) == 2 and "腐乳" in seq[1] and "咖啡" in seq[1], f"{d1['name']} {seq}")
        seq.clear(); BAD_N = [9]
        d2 = w3._analyze_checked(proj3, refs3, {"track": "美食", "ref": None, "top": None, "normal": None, "comparable": False})
        rep.check("重做后仍不行:换成通用名称", d2["name"] == "美食写法拆解" and len(seq) == 2, f"{d2['name']} {seq}")
    finally:
        llm3.analyze = orig_an

    print("\n[DeepSeek 偶发错误自动重试]")
    import httpx as hx
    from app import llm as llm5
    og, op, ow = llm5.get_settings, llm5.httpx.post, llm5.CHAT_WAITS
    llm5.get_settings = lambda: {"deepseek_api_key": "k", "deepseek_base_url": "https://x.test", "deepseek_model": "m"}
    llm5.CHAT_WAITS = (0, 0, 0)
    queue, nposts = [], []

    def fake_post(*a, **k):
        nposts.append(1)
        act = queue.pop(0)
        if act == "net":
            raise hx.ConnectError("boom")
        if isinstance(act, int):
            return hx.Response(act, text="err")
        return hx.Response(200, json={"choices": [{"message": {"content": act}}]})

    llm5.httpx.post = fake_post
    try:
        def go(actions):
            nposts.clear(); queue[:] = actions
            try:
                return llm5.chat("s", "u"), len(nposts), ""
            except RuntimeError as ex:
                return None, len(nposts), str(ex)
        r, n, err = go(['{"a": 1,\n}', '{"a": 1}'])
        rep.check("返回的 JSON 不合法:自动重试后成功", r == {"a": 1} and n == 2, f"{r} {n} {err}")
        r, n, err = go(["net", 503, '{"ok": true}'])
        rep.check("网络抖动 / 5xx:自动重试", r == {"ok": True} and n == 3, f"{r} {n} {err}")
        r, n, err = go([429, '{"ok": 1}'])
        rep.check("限流 429:自动重试", r == {"ok": 1} and n == 2, f"{r} {n} {err}")
        r, n, err = go([401])
        rep.check("Key 错误:不重试,直接报错", r is None and n == 1 and "401" in err, f"{n} {err}")
        r, n, err = go(["{bad", "{bad", "{bad"])
        rep.check("一直不合法:试 3 次后报错并说明", r is None and n == 3 and "已自动重试 3 次" in err, f"{n} {err}")
    finally:
        llm5.get_settings, llm5.httpx.post, llm5.CHAT_WAITS = og, op, ow

    print("\n[笔记主题]")
    from app import llm as llm6
    got, oc6 = {}, llm6.chat

    def fake_chat(system, user, temperature=1.0):
        got["system"], got["user"] = system, user
        return {"items": [{"id": 1, "type": "教程攻略", "topic": "生姜粉", "audience": "x", "highlight": "h", "tags": ["a"]},
                          {"id": 2, "type": "教程攻略", "audience": "x", "highlight": "h", "tags": []},
                          {"id": 3, "type": "教程攻略", "topic": "这是一个特别特别特别长的主题名称", "tags": []}]}
    llm6.chat = fake_chat
    try:
        out = llm6.tag_notes([{"id": i, "title": "t", "content": "c"} for i in (1, 2, 3)], ["姜黄粉", "速溶咖啡"], ["腐乳", "咖啡"])
    finally:
        llm6.chat = oc6
    rep.check("打标签:返回主题", out[1]["topic"] == "生姜粉")
    rep.check("打标签:模型没给主题时标「未识别」", out[2]["topic"] == "未识别")
    rep.check("打标签:主题过长会截断", len(out[3]["topic"]) <= 12)
    rep.check("打标签:已有主题带给模型,以便沿用同一写法", "已有主题:姜黄粉、速溶咖啡" in got["user"])
    rep.check("打标签:把搜索词告诉模型,并要求粒度粗、不带地名品牌", "搜索词:腐乳、咖啡" in got["user"] and "不要带地名、品牌名" in got["system"] and "不要硬套搜索词" in got["system"])
    rep.check("打标签:提示词要求按内容判断主题,并区分生姜与姜黄", "不要照抄搜索词" in got["system"] and "生姜和姜黄是两种不同的东西" in got["system"])

    print("\n[主题合并建议]")
    from app import llm as llm8
    oc8 = llm8.chat
    llm8.chat = lambda system, user, temperature=1.0: {"groups": [
        {"to": "咖啡兼职", "from": ["Manner兼职", "咖啡店兼职"]},          # 正常
        {"to": "旅游美食", "from": ["东山岛小吃", "不存在的主题"]},        # 只有 1 个真实主题 → 要补上「to」才够 2 个,这里不够,应丢弃
        {"to": "姜黄奶", "from": ["黄金奶"]},                              # to 本身是已有主题 → 合并成立
        {"to": "重复", "from": ["Manner兼职", "咖啡豆"]},                  # Manner兼职 已经用过 → 只剩 1 个,丢弃
        {"to": "", "from": ["咖啡豆", "咖啡液"]}]}                         # 没名字 → 丢弃
    try:
        items8 = [{"k": k, "n": 1, "samples": ["x"]} for k in ["Manner兼职", "咖啡店兼职", "东山岛小吃", "黄金奶", "姜黄奶", "咖啡豆", "咖啡液"]]
        g8 = llm8.suggest_topic_merges(items8)
    finally:
        llm8.chat = oc8
    rep.check("合并建议:校验后只保留成立的组", g8 == [{"to": "咖啡兼职", "from": ["Manner兼职", "咖啡店兼职"]}, {"to": "姜黄奶", "from": ["黄金奶"]}], str(g8))
    rep.check("合并建议:提示词要求不合并不同的东西(生姜粉≠姜黄粉)", "生姜粉和姜黄粉是两种东西" in llm8.MERGE_SYSTEM and "不带地名、品牌名" in llm8.MERGE_SYSTEM)

    print("\n[相关度 / 时间范围 / 关键词推荐]")
    import datetime as _dt
    from app import notes as nt9, llm as llm9, stats as st9
    rep.check("相关度:搜索词在标题里 → 相关", nt9.relevance({"keyword": "腐乳", "title": "自制腐乳教程", "content": "", "tags": []})[0] == "high")
    rep.check("相关度:只在正文里顺带提到 → 弱相关", nt9.relevance({"keyword": "腐乳", "title": "东山岛美食", "content": "还有腐乳饼", "tags": []})[0] == "low")
    rep.check("相关度:都没提到 → 弱相关", nt9.relevance({"keyword": "腐乳", "title": "穿搭", "content": "x", "tags": []})[0] == "low")
    rep.check("相关度:没有搜索词时不判断", nt9.relevance({"keyword": "", "title": "a", "content": "b", "tags": []})[0] == "high")
    rep.check("相关度:AI 主题和搜索词无关 → 弱相关", nt9.relevance({"keyword": "腐乳", "title": "腐乳", "topic": "旅游美食", "content": "", "tags": []})[0] == "low")
    today9 = _dt.date(2026, 11, 2)
    rep.check("时间:超过 180 天算旧", nt9.is_old("2026-03-01", 180, today9) and not nt9.is_old("2026-09-01", 180, today9))
    rep.check("时间:不限(0)/看不懂的不算旧", not nt9.is_old("2020-01-01", 0, today9) and not nt9.is_old("", 180, today9))
    kept9, drop9 = nt9.split_by_age([{"published_at": "2024-01-01"}, {"published_at": "2026-10-20"}, {"title": "无日期"}], 180, today9)
    rep.check("回传兜底:丢旧的、留新的和无日期的", drop9 == 1 and len(kept9) == 2, f"{drop9} {len(kept9)}")
    oc9 = llm9.chat
    llm9.chat = lambda system, user, temperature=1.0: {"broad": True, "suggestions": [{"word": "咖啡", "hint": "同词"}, {"word": "手冲咖啡教程", "hint": "a"}, {"word": "手冲咖啡教程", "hint": "重复"}, {"word": "x", "hint": ""}, {"word": "在家做咖啡", "hint": "b"}]}
    try:
        k9 = llm9.suggest_keywords("美食", "咖啡")
    finally:
        llm9.chat = oc9
    rep.check("关键词推荐:去掉原词/重复/过短", [i["word"] for i in k9["suggestions"]] == ["手冲咖啡教程", "在家做咖啡"] and k9["broad"], str(k9))
    fb9 = llm9.fallback_keywords("美食", "腐乳")
    rep.check("关键词推荐:AI 不可用时有兜底", "腐乳做法" in [i["word"] for i in fb9["suggestions"]])
    rep.check("提示词:允许第一人称体验和合理功效,不编造价格/数据", "第一人称" in llm9.NO_FABRICATION and "合理范围" in llm9.NO_FABRICATION and "价格" in llm9.NO_FABRICATION and "本平台内" in llm9.NO_FABRICATION and "治疗、治愈、根治" in llm9.NO_FABRICATION)

    print("\n[字数软约束]")
    from app import lengthcap as lc, worker as w10, llm as llm10
    st10 = {"track": "美食", "scope": "topic:腐乳", "comparable": True, "warnings": [], "top": {"body_len": 54}, "normal": {"body_len": 218}}
    long10 = "字" * 191
    p10 = lc.plan(st10, "腐乳种草日常", long10)
    rep.check("字数:超过 2.5 倍基准要压", p10 and p10["baseline"] == 54 and p10["cap"] == 135 and p10["target"] == 81, str(p10))
    rep.check("字数:没超标不压", lc.plan(st10, "腐乳种草日常", "字" * 130) is None)
    rep.check("字数:教程/清单类放宽到普通组,191 字不压", lc.plan(st10, "第一次买腐乳怎么挑,看配料表", long10) is None)
    rep.check("字数:样本不够不压", lc.plan({**st10, "comparable": False}, "x", long10) is None)
    rep.check("字数:参考不典型(基准不可信)不压", lc.plan({**st10, "warnings": ["差别很大"]}, "x", long10) is None)
    rep.check("字数:没有统计不压", lc.plan({}, "x", long10) is None and lc.plan(None, "x", long10) is None)
    out10 = {"title": "t", "content": "开头" + "字" * 188 + "【补充:价格】", "alt_titles": [], "tags": ["a"]}
    oc10 = llm10.chat
    try:
        llm10.chat = lambda system, user, temperature=1.0: {"content": "短版开头【补充:价格】"}
        new10, v10 = w10._maybe_compress(dict(out10), st10, "腐乳种草日常", True)
        rep.check("压缩成功:换成压缩版,原稿留在 variant", new10["content"] == "短版开头【补充:价格】" and v10["other"] == out10["content"] and v10["showing"] == "short" and v10["long_len"] == len(out10["content"]), str(v10)[:120])
        back10, v10b = lc.swap(new10["content"], v10)
        rep.check("切换:换回原稿,压缩版留着", back10 == out10["content"] and v10b["other"] == new10["content"] and v10b["showing"] == "long")
        again10, v10c = lc.swap(back10, v10b)
        rep.check("切换:再切回压缩版", again10 == new10["content"] and v10c["showing"] == "short")
        llm10.chat = lambda system, user, temperature=1.0: {"content": "短版开头,价格没了"}
        n2, v2 = w10._maybe_compress(dict(out10), st10, "腐乳种草日常", True)
        rep.check("压缩丢了占位符:放弃,保留原稿", n2["content"] == out10["content"] and v2 == {})
        llm10.chat = lambda system, user, temperature=1.0: {"content": out10["content"][:-3] + "【补充:价格】"}
        n3, v3 = w10._maybe_compress(dict(out10), st10, "腐乳种草日常", True)
        rep.check("压缩后没变短:放弃", v3 == {})
        def boom10(*a, **k): raise RuntimeError("x")
        llm10.chat = boom10
        n4, v4 = w10._maybe_compress(dict(out10), st10, "腐乳种草日常", True)
        rep.check("压缩调用失败:保留原稿,不影响出稿", n4["content"] == out10["content"] and v4 == {})
    finally:
        llm10.chat = oc10
    n5, v5 = w10._maybe_compress(dict(out10), st10, "腐乳种草日常", False)
    rep.check("设置里关掉:不压缩", v5 == {} and n5["content"] == out10["content"])
    rep.check("提示词:占位符原样保留、不新增事实", "占位符必须原样保留" in llm10.COMPRESS_SYSTEM and "不增加原文没有的新事实" in llm10.COMPRESS_SYSTEM)

    print("\n[导入]")
    rows = parse_file("a.csv", "标题,正文,点赞,话题\n标题一,正文一,1.5万,#a #b\n".encode("utf-8"))
    rep.check("CSV 中文表头", rows and rows[0]["title"] == "标题一" and rows[0]["likes"] == "1.5万", str(rows))
