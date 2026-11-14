"""商家账号不装买家:买家口吻(我买了 / 亲测 / 用了 N 天 / 求推荐…)算高风险,触发自动改写;商家口吻和无主语的建议不误报;提示词和类型要求按这个写。"""
import json


def run(rep, env):
    from app import compliance, db, llm, ptype, worker

    print("\n[商家口吻 / 买家口吻]")

    def cats(title, content):
        r = compliance.scan(title, content, [])
        return [h for h in r["hits"] if h["category"] == compliance.CATEGORIES["buyer"][0]], r

    buyer = {"我买了三款回来试": 1, "亲测有效,真的好吃": 1, "我用了两周,胃里暖暖的": 1, "坚持了3个月": 1, "求推荐一款不齁的腐乳": 1, "有没有人知道哪里买": 1,
             "闺蜜推荐我买的": 1, "我的感受是很绵密": 1, "回购了好几次": 1, "盲测排名放这里": 1}
    bad = [t for t in buyer if not cats("", t)[0]]
    rep.check("买家口吻命中(高风险):我买了 / 亲测 / 用了 N 周 / 求推荐 / 闺蜜推荐 / 我的感受 / 回购 / 盲测 都能抓到", not bad, f"没抓到:{bad}")
    ok_texts = ["我们家做了十年腐乳,选料很讲究", "建议你这样吃:热米饭拌一勺", "我们按咸度、绵密度、香气三个维度整理了对比", "第一步先热锅,倒油", "买一小罐腐乳,配粥最合适",
                "可以这样搭配:白粥 + 腐乳 + 一碟小菜", "你可以先尝一小口,不习惯再加量", "油温六成热再下锅"]
    fp = [t for t in ok_texts if cats("", t)[0]]
    rep.check("商家口吻、无主语建议、教程步骤不误报", not fp, f"误报:{fp}")
    h1, r1 = cats("", "我平时这样吃腐乳拌饭,很下饭")
    h2, r2 = cats("", "我们家做了十年腐乳。我平时这样吃腐乳拌饭,很下饭")
    rep.check("「我平时这样吃」这类习惯说法:没亮明商家身份算买家口吻;亮明了(我们家 / 做了几年…)放过", len(h1) == 1 and not h2, str((h1, h2)))
    h3, _ = cats("", "我买了【补充:型号】试了试")
    rep.check("带【补充:…】占位符的句子不报(留给作者填)", not h3)
    h4, r4 = cats("亲测!腐乳拌饭", "我买了一罐")
    rep.check("命中带字段和替换建议;计入高风险(会触发自动改写、审稿扣分);标题里的也抓", {h["field"] for h in h4} == {"title", "content"} and r4["high"] >= 2 and all("商家口吻" in h["replacement"] for h in h4), str(h4))

    # 自动改写:命中买家口吻就改一次,改成商家口吻后复扫通过
    o_revise = llm.revise
    seen = {}
    llm.revise = lambda out, hits: seen.update(hits=hits) or {"title": "腐乳拌饭这样吃", "content": "我们家做了十年腐乳。拌饭可以这样吃:热米饭加一勺。"}
    try:
        out, checks = worker._check_and_fix({"title": "亲测!腐乳拌饭", "content": "我买了一罐,用了两周", "tags": ["a"]}, auto=True)
    finally:
        llm.revise = o_revise
    rep.check("自动改写:买家口吻触发改写(把命中的说法交给模型),改后复扫没有高风险", any(h["category"] == compliance.CATEGORIES["buyer"][0] for h in seen.get("hits", [])) and checks["high"] == 0 and checks["fixed"] >= 1 and "我们家" in out["content"], str((seen, checks)))

    # 提示词
    rep.check("写稿提示词:不再鼓励第一人称买家体验,改成「商家 / 科普 / 对读者」三种口吻,并列出禁用说法",
              "默认视为作者本人的体验" not in llm.WRITE_SYSTEM and "不能装成普通买家" in llm.WRITE_SYSTEM and "我们家" in llm.WRITE_SYSTEM and "求推荐" in llm.WRITE_SYSTEM and "亲测" in llm.WRITE_SYSTEM)
    rep.check("改稿提示词有买家口吻规则;审稿提示词必查买家口吻(算 high)", "买家口吻" in llm.REVISE_SYSTEM and "必查:买家口吻" in llm.REVIEW_SYSTEM)
    try:
        llm.WRITE_SYSTEM.format(banned="x"), llm.REVIEW_SYSTEM.format(banned="x", comments_rule="", comments_field=""), llm.REVISE_SYSTEM
        fmt_ok = True
    except Exception:
        fmt_ok = False
    rep.check("提示词模板仍能正常格式化(新增的文字里没有会冲突的花括号)", fmt_ok)
    rep.check("类型要求:软文 / 测评不再要求买家第一人称,明确不能装成买家", "不能装成买家" in ptype.RULES["产品软文"] and "亲测" in ptype.RULES["测评"] and "商家口吻" in ptype.RULES["测评"])

    # 软文被判成商业硬文是对的(商家口吻),不再逼着改回买家口吻
    pj = db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('美食','[]','')").lastrowid
    did = db.q_exec("INSERT INTO drafts(project_id,title,content,status) VALUES(?,?,?,'ready')", pj, "t", "c").lastrowid
    calls = []
    o_check, o_write = ptype.check, llm.write_note
    ptype.check = lambda title, content: "商业硬文"
    llm.write_note = lambda *a, **k: calls.append(1) or {"title": "x", "content": "y"}
    try:
        out = worker._match_ptype({}, {"kind": "产品软文", "data": {}}, [], {"id": did, "extra": "", "angle": ""}, {"title": "t", "content": "c"}, "", "", "")
        n_soft = len(calls)
        ptype.check = lambda title, content: "教程"
        worker._match_ptype({}, {"kind": "产品软文", "data": {}}, [], {"id": did, "extra": "", "angle": ""}, {"title": "t", "content": "c"}, "", "", "")
    finally:
        ptype.check, llm.write_note = o_check, o_write
    rep.check("类型校验:产品软文被判成商业硬文不重写(商家口吻的软文就该是这样);判成教程仍然重写", n_soft == 0 and len(calls) == 1, str((n_soft, calls)))

    # ---- 给已有稿子补扫(迁移)----
    pj2 = db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('美食','[]','')").lastrowid
    mk = lambda t, c, ok, pub=0, ck="{}": db.q_exec("INSERT INTO drafts(project_id,title,content,status,auto_ok,auto_note,published,checks) VALUES(?,?,?,'ready',?,'',?,?)", pj2, t, c, ok, pub, ck).lastrowid
    d_bad = mk("腐乳拌饭", "我买了一罐,亲测好吃", 1)
    d_good = mk("腐乳拌饭", "我们家做了十年腐乳。拌饭可以这样吃。", 1)
    d_pub = mk("腐乳粥", "回购了好几次", 1, 1, json.dumps({"hits": [{"word": "最好", "field": "content", "count": 1, "category": "广告法极限词", "severity": "high"}], "high": 1, "low": 0}))
    with db.conn() as c:
        db._m020_buyer_voice(c)
    rows = {i: db.q_one("SELECT checks, auto_ok, auto_note FROM drafts WHERE id=?", i) for i in (d_bad, d_good, d_pub)}
    bcat = compliance.CATEGORIES["buyer"][0]
    rep.check("补扫:含买家口吻的老稿子质检结果里补上这一项并算入高风险,原来「质检合格」的改成需检查并写明原因;没问题的稿子不动",
              any(h["category"] == bcat for h in rows[d_bad]["checks"]["hits"]) and rows[d_bad]["checks"]["high"] >= 2 and rows[d_bad]["auto_ok"] == 0 and "买家口吻" in rows[d_bad]["auto_note"]
              and rows[d_good]["auto_ok"] == 1 and not rows[d_good]["checks"].get("hits"), str(rows))
    rep.check("补扫:已有的别的风险项保留,买家口吻追加;已发布的也补上记录", any(h["category"] == "广告法极限词" for h in rows[d_pub]["checks"]["hits"]) and any(h["category"] == bcat for h in rows[d_pub]["checks"]["hits"])
              and rows[d_pub]["checks"]["high"] == 2)
    snap = {i: db.q_one("SELECT checks, auto_ok FROM drafts WHERE id=?", i) for i in (d_bad, d_good, d_pub)}
    with db.conn() as c:
        db._m020_buyer_voice(c)
    rep.check("补扫:重复执行结果不变(不会重复追加)", snap == {i: db.q_one("SELECT checks, auto_ok FROM drafts WHERE id=?", i) for i in (d_bad, d_good, d_pub)})
