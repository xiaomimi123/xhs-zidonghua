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
    rep.check("提示词里不再有「喝完胃里暖暖的」「对我来说」这类第一人称体验的示例(和禁止买家口吻互相矛盾)", "喝完胃里暖暖的" not in nf and "对我来说" not in nf and "不写自己或别人亲身喝了之后的变化" in nf)
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
