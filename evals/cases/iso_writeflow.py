"""写稿框架审查后的修复:身份规则统一(知识库 / 提示词 / 审稿 / 拆解卡不再自相矛盾)、重写不丢帖子类型要求。
大模型相关的效果另外用真实生成对比验证(见提交说明),这里只测代码和提示词里的约束。"""
import json


def run(rep, env):
    from app import db, kb, llm, ptype, worker, compliance

    print("\n[写稿框架:身份规则统一]")
    # ---- 提示词里不能再有互相矛盾的说法 ----
    ws, nf = llm.WRITE_SYSTEM, llm.NO_FABRICATION
    rep.check("身份规则优先于拆解卡的语气 / 钩子和知识库;写稿提示词说明拆解卡的语气、钩子只学结构,身份一律商家 / 中立口吻",
              "优先于「拆解卡」里的语气和开头钩子" in nf and "知识库里关于「第一人称」的任何说法" in nf and "只学它的结构和节奏" in ws and "身份一律按下面的身份规则改成商家 / 中立口吻" in ws)
    rep.check("提示词里不再有「喝完胃里暖暖的」「对我来说」这类第一人称体验的示例(和禁止买家口吻互相矛盾)", "喝完胃里暖暖的" not in nf and "对我来说" not in nf and "身体变化" in nf)
    rep.check("审稿提示词不再说「作者的使用感受可以写,不算问题」(和「买家口吻是 high 问题」矛盾)", "作者的使用感受" not in llm.REVIEW_SYSTEM and "使用感受只能按身份规则" in llm.REVIEW_SYSTEM)
    rep.check("拆解提示词:tone 要写成商家 / 中立口吻,不写买家身份;hooks 只写结构,示例里不出现「我买了 / 我喝了」", "适合商家 / 品牌账号的口吻" in llm.ANALYZE_SYSTEM and "示例里不要出现「我买了 / 我喝了 / 我用了」" in llm.ANALYZE_SYSTEM)
    rep.check("产品软文类型要求:不再写「用起来的感受、个人看法」(和禁止「我的感受」矛盾)", "用起来的感受" not in ptype.RULES["产品软文"] and "个人看法" not in ptype.RULES["产品软文"] and "我们为什么做成这样" in ptype.RULES["产品软文"])
    got = {}
    o_chat = llm.chat
    llm.chat = lambda system, user, temperature=1.0: got.update(user=user, system=system) or {"title": "t", "content": "c"}
    try:
        llm.write_note({"track": "美食", "keywords": ["腐乳"]}, {"tone": "闺蜜分享"}, [{"title": "a", "content": "b"}], "角度", "", "", "")
    finally:
        llm.chat = o_chat
    rep.check("写稿用户消息:拆解卡和参考笔记的说明里写明「买家口吻,只学结构」", "语气、钩子示例是买家口吻,只学结构" in got["user"] and "不学买家身份" in got["user"], got["user"][:160])

    # ---- 知识库:线上已存的通用规则里的旧说法会被换成商家口吻 ----
    db.save_settings({"kb_common": "# 通用\n" + kb.OLD_IDENTITY + "\n- 管理员自己加的一条"})
    with db.conn() as c:
        kb.seed(c)
    v = db.get_settings()["kb_common"]
    rep.check("知识库迁移:通用规则里「第一人称的使用感受可以写」换成商家口吻,管理员自己加的内容不动;重复执行不变",
              "第一人称的使用感受、吃法、价格可以写" not in v and kb.KB_IDENTITY in v and "管理员自己加的一条" in v, v[:120])
    with db.conn() as c:
        kb.seed(c)
    rep.check("知识库迁移重复执行结果不变", db.get_settings()["kb_common"] == v)
    rep.check("新装的默认通用规则也是商家口吻", "第一人称的使用感受、吃法、价格可以写" not in kb.KB_COMMON and kb.KB_IDENTITY in kb.KB_COMMON)

    # ---- 重写(选题冲突)要带上帖子类型要求 ----
    print("\n[写稿框架:重写不丢类型要求]")
    from app.notes import save_notes
    db.save_settings({"auto_review": "0", "auto_fix": "0"})
    pj = db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('美食',?,'')", json.dumps(["腐乳", "咖啡"], ensure_ascii=False)).lastrowid
    save_notes([{"note_id": "WF1", "title": "腐乳怎么吃", "content": "腐乳配粥很下饭", "likes": 1}, {"note_id": "WF2", "title": "冻干咖啡", "content": "冻干咖啡冲泡方法", "likes": 1}], project_id=pj)
    nids = [r["id"] for r in db.q_all("SELECT id FROM notes WHERE uid IN ('WF1','WF2')")]
    pb = db.q_exec("INSERT INTO playbooks(project_id,name,note_ids,data,status,kind) VALUES(?,?,?,?,'done','产品软文')", pj, "类型测试", json.dumps(nids), json.dumps({"topics": ["咖啡"]})).lastrowid
    did = db.q_exec("INSERT INTO drafts(project_id,playbook_id,angle,extra,status,with_images,ptype) VALUES(?,?,?,?,'queued',0,'产品软文')", pj, pb, "主题:咖啡", "").lastrowid
    extras, replies = [], [{"title": "腐乳配咖啡", "content": "咖啡加腐乳一起吃"}, {"title": "咖啡的三种冲法", "content": "冻干咖啡冲泡"}]
    o_w, o_c = llm.write_note, ptype.check
    llm.write_note = lambda project, pbd, refs, angle, extra="", kb="", banned="": extras.append(extra) or {"alt_titles": [], "tags": ["a"], "image_prompts": [], **replies[min(len(extras), 2) - 1]}
    ptype.check = lambda title, content: "产品软文"
    try:
        worker._run_text(did)
    finally:
        llm.write_note, ptype.check = o_w, o_c
    r = db.q_one("SELECT title, ptype_got FROM drafts WHERE id=?", did)
    rep.check("选题冲突重写:第一次和重写的要求里都带着帖子类型(【帖子类型:产品软文】),不再丢掉",
              len(extras) == 2 and all("【帖子类型:产品软文】" in e for e in extras) and "腐乳" in extras[1], str([e[-80:] for e in extras]))
    rep.check("重写后重新判一次类型:ptype_got 记的是最终这一版,标题是重写后的", r["title"] == "咖啡的三种冲法" and r["ptype_got"] == "产品软文", str(r))


    # ---- 类型复检:按结构判,不把商家口吻直接当商业硬文 ----
    print("\n[写稿框架:类型复检]")
    seen = {}
    o_chat = llm.chat
    llm.chat = lambda system, user, temperature=1.0: seen.update(system=system, user=user) or {"kind": "教程", "why": "有步骤"}
    try:
        k1 = ptype.check("肉桂拿铁怎么做", "材料:咖啡2g、牛奶200ml\n1. 冲咖啡\n2. 打奶泡")
        llm.chat = lambda system, user, temperature=1.0: {"kind": "乱写的类型"}
        k2 = ptype.check("t", "c")
        llm.chat = lambda system, user, temperature=1.0: {}
        k3 = ptype.check("t", "c")
    finally:
        llm.chat = o_chat
    rep.check("复检:返回模型判的类型;类型不在列表里 / 没返回都当判不出(空串)", k1 == "教程" and k2 == "" and k3 == "")
    rep.check("复检用的是「我们自己写的稿子」的标准(不看口吻、只看结构),不是采集笔记那套;把规则线索(价格购买词 / 步骤用量词 / 评价词)一起给模型",
              seen["system"] is ptype.OWN_SYSTEM and "不要因为出现「我们家」就判商业硬文" not in ptype.KIND_SYSTEM and "口吻不是判断依据" in ptype.OWN_SYSTEM
              and "线索:价格购买词" in seen["user"] and "步骤用量词" in seen["user"], seen["user"][-60:])
    rep.check("复检标准里五个类型都有判断规则,并写明判断步骤(先评价结构、再步骤用量、再产品信息占比)", all(k in ptype.OWN_SYSTEM for k in ("- 教程", "- 测评", "- 商业硬文", "- 产品软文", "- 其他")) and "判断步骤" in ptype.OWN_SYSTEM)
    rep.check("采集笔记的分类标准没动(商家口吻仍算商业硬文,用于参考笔记分类)", "商业硬文补充:商家 / 品牌账号推广自家产品" in ptype.KIND_SYSTEM)

    # ---- 功效和产品资料:AI 推断的内容不再冒充作者提供的真实资料 ----
    print("\n[写稿框架:功效和产品资料]")
    from app import products
    rep.check("功效:作者资料里有的按原话写,没有的不自己加(列出抗炎 / 调理肠胃 / 祛痘…),不写喝完之后的身体变化;不再说功效是「爆款关键」",
              "作者资料(卖点、产品资料)里写了的作用,按原话写" in nf and "资料里没写的,不要自己写具体功效" in nf and "身体变化" in nf and "这是爆款笔记吸引人的关键" not in nf)
    rep.check("知识库通用规则里「可以在合理范围内写功效和使用感受」换成「功效只写作者资料里提供的」(新装和线上已存的都换)",
              "可以在合理范围内写功效和使用感受" not in kb.KB_COMMON and "功效只写作者资料里提供的" in kb.KB_COMMON)
    db.save_settings({"kb_common": "# 通用\n- 不站外导流;可以在合理范围内写功效和使用感受。\n- 管理员加的"})
    with db.conn() as c:
        kb.seed(c)
    v = db.get_settings()["kb_common"]
    rep.check("线上已存的通用规则启动时也换成新说法,管理员加的内容不动", "功效只写作者资料里提供的" in v and "可以在合理范围内写功效" not in v and "管理员加的" in v, v)
    spec = {"kind": "drink", "serve": "hot", "pairings": [{"name": "热红酒", "freq": "high"}], "avoid": [], "shoot_note": "", "size": "AI 估:罐高 9cm"}
    blk = products.block({"id": 0, "name": "测试肉桂粉", "selling_points": "原料为锡兰肉桂", "price": "39.8", "audience": "爱喝拿铁的人", "category": "肉桂粉",
                          "profile": {"appearance": "红色铁盒", "package_text": "有机认证", "scenes": ["健身房"], "shoot": spec}, "size_note": ""})
    rep.check("产品资料块:AI 看图 / 推断的外观、包装上的字、场景、搭配都标了「AI」并说明拿不准的不要写进正文;作者提供的(卖点、价格)不标;开头不再说「以下是作者提供的真实产品资料」",
              "外观(AI 看图推断,仅供参考)" in blk and "包装上的字(AI 看图识别,可能认错,不确定的不要写)" in blk and "可以带出产品的场景(AI 推断,仅供参考)" in blk
              and "搭配和吃法(AI 推断" in blk and "- 卖点(作者提供):原料为锡兰肉桂" in blk and "拿不准的不要写进正文" in blk and "以下是作者提供的真实产品资料" not in blk, blk[:300])

    # ---- 规则冲突清理 ----
    print("\n[写稿框架:规则冲突清理]")
    from app import covers
    rep.check("美食知识库:「必写用量、火候、时间」只对教程要求,软文 / 测评 / 商业硬文按各自类型要求(不再和软文的「禁止具体用量」冲突)",
              "教程类必写用量、火候、时间" in kb.TRACK_KB["美食"] and "必写用量、火候、时间;" not in kb.TRACK_KB["美食"] and "不堆用量和步骤" in kb.TRACK_KB["美食"])
    db.q_exec("UPDATE tracks SET knowledge=? WHERE name='美食'", "- 标题常用:时间 + 菜名\n- " + kb.OLD_FOOD_LINE + "\n- 管理员加的")
    with db.conn() as c:
        kb.seed(c)
    kn = db.q_one("SELECT knowledge FROM tracks WHERE name='美食'")["knowledge"]
    rep.check("线上已存的美食知识库启动时也换成新说法,管理员加的内容不动;重复执行不变", kb.NEW_FOOD_LINE in kn and kb.OLD_FOOD_LINE not in kn and "管理员加的" in kn)
    with db.conn() as c:
        kb.seed(c)
    rep.check("美食知识库迁移重复执行结果不变", db.q_one("SELECT knowledge FROM tracks WHERE name='美食'")["knowledge"] == kn)
    rep.check("产品资料块:「不要写成硬广」不适用于商业硬文;商业硬文类型要求写明产品是主角、优先于「自然植入」、产品信息占三分之一以上",
              "除非帖子类型是商业硬文" in blk and "优先于「产品自然植入、不要写成硬广」" in ptype.RULES["商业硬文"] and "产品信息要占正文三分之一以上" in ptype.RULES["商业硬文"])
    pr0 = covers.inner_prompts([], 2, "美食", 1, "标题ABC稿子标题", {"name": "测试肉桂粉"})
    pr1 = covers.inner_prompts([], 1, "美食", 1, "标题ABC稿子标题", None)
    rep.check("配图提示词兜底:模型少给提示词时,用产品名(没有产品用赛道)当画面主体,不再把稿子标题当画面描述",
              len(pr0) == 2 and all("测试肉桂粉的主体特写" in x and "标题ABC" not in x for x in pr0) and "美食的主体特写" in pr1[0] and "标题ABC" not in pr1[0], str(pr0[:1]))
    # 拆解卡没有类型(凑不出分类时):有产品的任务按产品软文写,没有产品的不加类型要求
    from app.notes import save_notes
    db.save_settings({"auto_review": "0", "auto_fix": "0"})
    save_notes([{"note_id": "WK1", "title": "咖啡怎么冲", "content": "冻干咖啡冲泡方法 " * 5, "likes": 1}], project_id=None)
    nid = db.q_one("SELECT id FROM notes WHERE uid='WK1'")["id"]
    prod_id = db.q_exec("INSERT INTO products(name,images) VALUES('测试咖啡','[]')").lastrowid
    seen_extra = []
    o_w = llm.write_note
    llm.write_note = lambda project, pbd, refs, angle, extra="", kb="", banned="": seen_extra.append(extra) or {"title": "t", "content": "c", "alt_titles": [], "tags": ["a"], "image_prompts": []}
    try:
        for with_prod in (True, False):
            pj2 = db.q_exec("INSERT INTO projects(track,keywords,direction,product_id) VALUES('美食','[]','',?)", prod_id if with_prod else None).lastrowid
            pb2 = db.q_exec("INSERT INTO playbooks(project_id,name,note_ids,data,status,kind) VALUES(?,?,?,?,'done','')", pj2, "无类型", json.dumps([nid]), json.dumps({})).lastrowid
            d2 = db.q_exec("INSERT INTO drafts(project_id,playbook_id,angle,extra,status,with_images) VALUES(?,?,?,?,'queued',0)", pj2, pb2, "主题:咖啡", "").lastrowid
            worker._run_text(d2)
    finally:
        llm.write_note = o_w
    rep.check("拆解卡没有类型:有产品的任务带「产品软文」类型要求,没有产品的任务不加",
              len(seen_extra) == 2 and "【帖子类型:产品软文】" in seen_extra[0] and "【帖子类型:" not in seen_extra[1], str([e[-60:] for e in seen_extra]))

