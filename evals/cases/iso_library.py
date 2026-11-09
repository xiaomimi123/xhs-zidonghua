"""封面与重复笔记、笔记库优先、采集任务队列(领取顺序、回传、补全赛道、关键词扩展、续租)。"""
import json
from app import db
from pathlib import Path


def run(rep, env):
    _tf12, au, au13, d12, got12, ig12, pid11, ppj, q, sn11, today11, w4, wk11 = env._tf12, env.au, env.au13, env.d12, env.got12, env.ig12, env.pid11, env.ppj, env.q, env.sn11, env.today11, env.w4, env.wk11
    # 封面:程序排版,配图张数随机
    from app import covers as cv2, cards as cd2
    rep.check("场景库:同一篇里不同位置换不同场景", len({cv2.scene("美食", 7, k) for k in range(1, 4)}) == 3 and cv2.scene("美食", 3, 1) != cv2.scene("穿搭", 3, 1))
    rep.check("配图提示词:内页可带场景方向", "场景:早餐桌。" in w4._image_prompt("t", "p", True, "早餐桌") and "场景:" not in w4._image_prompt("t", "p", True, ""))
    import app.llm as _l2
    _bak = _l2.chat
    _l2.chat = lambda sysm, user, temperature=1.0: {"image_prompts": ["一杯结团的奶茶特写", "过滤用的细网筛", "多余"]}
    try:
        _ip = _l2.inner_image_prompts("t", "正文", 2)
    finally:
        _l2.chat = _bak
    rep.check("按正文重策划内页提示词:条数裁剪", _ip == ["一杯结团的奶茶特写", "过滤用的细网筛"], str(_ip))
    rep.check("场景库里没有无关的礼盒", not any("礼盒" in x for x in cv2.FOOD_SCENES + cv2.GENERIC_SCENES))
    rep.check("告诉写作模型本篇共几张图", "【配图数量】本篇配图共 3 张" in w4.image_block(3) and "没有内页图" in w4.image_block(1))
    rep.check("写作时带上配图数量", "【配图数量】" in got12.get("extra", ""), got12.get("extra", "")[-80:])
    r12d = q("SELECT image_prompts, cover_card FROM drafts WHERE id=?", d12)[0]
    rep.check("写完后存下封面信息和 1~4 条配图说明", r12d["cover_card"].get("layout") in cd2.LAYOUTS and 1 <= len(r12d["image_prompts"]) <= 4 and r12d["image_prompts"][0].startswith("封面·"), str(r12d)[:200])
    # 重复笔记
    from app.notes import mark_duplicates as md14
    pjd = db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('美食','[\"腐乳做法\"]','')").lastrowid
    sn11([{"note_id": "DUP1", "title": "在亲戚家吃过一回,被惊艳到了😭!!", "content": "短正文一", "author": "番茄美食记", "likes": 300, "published_at": today11},
          {"note_id": "DUP2", "title": "在亲戚家吃过一回，被惊艳到了！", "content": "短正文二", "author": "番茄美食记", "likes": 500, "published_at": today11},
          {"note_id": "DUP3", "title": "在亲戚家吃过一回被惊艳到了", "content": "别的号搬运", "author": "另一个号", "likes": 100, "published_at": today11},
          {"note_id": "DUP4", "title": "腐乳做法第一期:红烧肉", "content": "x", "author": "老王", "likes": 50, "published_at": today11},
          {"note_id": "DUP5", "title": "腐乳做法第二期:空心菜", "content": "y", "author": "老王", "likes": 60, "published_at": today11}], project_id=pjd, keyword="腐乳做法", track="美食")
    dd = {r["uid"]: r for r in q("SELECT uid,id,dup_of FROM notes WHERE uid LIKE 'DUP%'")}
    rep.check("重复笔记:标题去掉表情标点后相同的归为一组,互动最高的当代表", dd["DUP2"]["dup_of"] is None and dd["DUP1"]["dup_of"] == dd["DUP2"]["id"] and dd["DUP3"]["dup_of"] == dd["DUP2"]["id"], str(dd))
    rep.check("重复笔记:同一博主的系列(标题只差几个字)不算重复", dd["DUP4"]["dup_of"] is None and dd["DUP5"]["dup_of"] is None)
    rows14 = [r["id"] for r in q("SELECT n.id FROM project_notes pn JOIN notes n ON n.id=pn.note_id WHERE pn.project_id=?" + " AND (n.dup_of IS NULL OR n.dup_of NOT IN (SELECT note_id FROM project_notes WHERE project_id=pn.project_id))", pjd)]
    rep.check("重复笔记:拆解选参考时排除重复(代表也在本项目时)", dd["DUP1"]["id"] not in rows14 and dd["DUP3"]["id"] not in rows14 and dd["DUP2"]["id"] in rows14)
    db.q_exec("UPDATE notes SET likes=900 WHERE uid='DUP3'"); md14()
    dd = {r["uid"]: r for r in q("SELECT uid,id,dup_of FROM notes WHERE uid LIKE 'DUP%'")}
    rep.check("重复笔记:互动数据刷新后代表会换", dd["DUP3"]["dup_of"] is None and dd["DUP2"]["dup_of"] == dd["DUP3"]["id"])
    # 笔记库优先 / 参考轮换 / 采集端查已有
    from app import api_collector as ac15
    db.save_settings({"deepseek_api_key": "test-key", "auto_library_min": "15", "auto_refs": "5", "auto_tag": "0", "auto_min_notes": "3", "auto_min_relevant": "3"})
    sn11([{"note_id": f"LL{i}", "title": f"榴莲酥做法第{i}种", "content": "榴莲酥" * 5, "likes": 1000 - i * 10, "published_at": today11} for i in range(18)]
         + [{"note_id": "LLOLD", "title": "榴莲酥老做法", "content": "x", "likes": 9999, "published_at": "2020-01-01"},
            {"note_id": "LLX", "title": "芒果班戟", "content": "榴莲酥只是顺带提一句", "likes": 8888, "published_at": today11}], keyword="榴莲酥", track="美食")
    m15 = au.library_match(["榴莲酥做法"], db.get_settings())
    uid15 = {r["id"]: r["uid"] for r in q("SELECT id,uid FROM notes WHERE uid LIKE 'LL%'")}
    rep.check("笔记库匹配:只要相关、近期的,按互动排序", len(m15) == 18 and uid15[m15[0]] == "LL0" and "LLOLD" not in {uid15.get(i) for i in m15} and "LLX" not in {uid15.get(i) for i in m15}, str([uid15.get(i) for i in m15][:4]))
    r15 = au13.auto_start(au13.AutoIn(track="美食", keywords=["榴莲酥做法"], count=2))
    rep.check("一键生成:笔记库够 15 篇就跳过采集", r15["collect"] is False and r15["library"] == 18 and q("SELECT COUNT(*) c FROM crawl_tasks WHERE project_id=?", r15["id"])[0]["c"] == 0
              and "跳过采集" in q("SELECT msg FROM autoruns WHERE project_id=?", r15["id"])[0]["msg"], str(r15))
    o_sp15, o_pas15 = wk11.submit_playbook, wk11.plan_and_submit
    wk11.submit_playbook = lambda pb_id: None
    wk11.plan_and_submit = lambda *a, **k: []
    try:
        au.tick(); au.tick()  # collecting(没有任务,直接过)→ tagging → 选参考
        st15 = q("SELECT status,state FROM autoruns WHERE project_id=?", r15["id"])[0]
        pb15 = q("SELECT note_ids FROM playbooks WHERE id=?", st15["state"]["pbs"][0])[0]["note_ids"]
        rep.check("复用笔记库后直接进入拆解,取互动最高的 5 篇", st15["status"] == "analyzing" and [uid15[i] for i in pb15] == ["LL0", "LL1", "LL2", "LL3", "LL4"] and st15["state"]["reused"] == 0, f"{st15['status']} {[uid15.get(i) for i in pb15]}")
        r16 = au13.auto_start(au13.AutoIn(track="美食", keywords=["榴莲酥做法"], count=2))
        au.tick(); au.tick()
        st16 = q("SELECT status,state,msg FROM autoruns WHERE project_id=?", r16["id"])[0]
        pb16 = q("SELECT note_ids FROM playbooks WHERE id=?", st16["state"]["pbs"][0])[0]["note_ids"]
        rep.check("参考轮换:下一批优先用没当过参考的爆款", [uid15[i] for i in pb16] == ["LL5", "LL6", "LL7", "LL8", "LL9"] and st16["state"]["reused"] == 0, str([uid15.get(i) for i in pb16]))
        r17 = au13.auto_start(au13.AutoIn(track="美食", keywords=["榴莲酥做法"], count=2))
        au.tick(); au.tick()
        st17 = q("SELECT state,msg FROM autoruns WHERE project_id=?", r17["id"])[0]
        pb17 = q("SELECT note_ids FROM playbooks WHERE id=?", st17["state"]["pbs"][0])[0]["note_ids"]
        rep.check("参考轮换:前 15 篇都用过了,说明有几篇是旧参考", [uid15[i] for i in pb17] == ["LL10", "LL11", "LL12", "LL13", "LL14"] and st17["state"]["reused"] == 0, str([uid15.get(i) for i in pb17]))
        r18 = au13.auto_start(au13.AutoIn(track="美食", keywords=["榴莲酥做法"], count=2))
        au.tick(); au.tick()
        st18 = q("SELECT state,msg FROM autoruns WHERE project_id=?", r18["id"])[0]
        rep.check("参考全是用过的:提示补采新笔记", st18["state"]["reused"] == 5 and "建议补采新笔记" in st18["msg"], st18["msg"])
    finally:
        wk11.submit_playbook, wk11.plan_and_submit = o_sp15, o_pas15
    db.save_settings({"auto_library_min": "999"})
    r19 = au13.auto_start(au13.AutoIn(track="美食", keywords=["榴莲酥做法"], count=2))
    rep.check("笔记库不够:先关联已有的,再给采集端下任务", r19["collect"] is True and r19["library"] == 18 and q("SELECT COUNT(*) c FROM project_notes WHERE project_id=?", r19["id"])[0]["c"] == 18
              and q("SELECT COUNT(*) c FROM crawl_tasks WHERE project_id=?", r19["id"])[0]["c"] == 1)
    au.cancel(r19["id"])
    db.save_settings({"auto_library_min": "15"})

    class _Req15:
        def __init__(self, body): self._b = body; self.query_params = {}; self.client = None
        async def json(self): return self._b
    import asyncio as _as15
    kn = _as15.run(ac15.known_notes(_Req15({"ids": ["LL0", "LL1", "全新的笔记", "LL0"]})))
    rep.check("采集端查已有笔记:分出已有和新的", kn["known"] == ["LL0", "LL1", "LL0"] and kn["new"] == ["全新的笔记"], str(kn))
    rep.check("任务带 skip_known、接口版本 1.4", "skip_known" in open(ac15.__file__, encoding="utf-8").read() and '"api_version": "1.4"' in open(ac15.__file__, encoding="utf-8").read())
    # 程序封面的出图流程
    from app import cards as cd2
    db.save_settings({"product_real_slots": "0", "product_max_refs": "2"})
    o_gen3, o_render3, o_dir3, o_sub3 = ig12.generate_image, cd2.render, ig12.IMG_DIR, w4.submit_images
    ig12.IMG_DIR = Path(_tf12.mkdtemp())
    gen3, ren3 = [], []
    def fake_gen3(prompt, name, s=None, refs=None):
        gen3.append((prompt, refs)); (ig12.IMG_DIR / f"{name}.png").write_bytes(b"x"); return f"{name}.png"
    def fake_render3(layout, theme, data, out, photo=None):
        ren3.append((layout, theme, photo)); Path(out).write_bytes(b"x"); return out
    ig12.generate_image, cd2.render = fake_gen3, fake_render3
    try:
        cc_text = {"layout": "statement", "theme": "ink", "title": "标题", "scene": "桌面"}
        d20 = db.q_exec("INSERT INTO drafts(project_id,title,image_prompts,images,cover_card,status,with_images) VALUES(?,?,?,'[]',?,'queued',1)", ppj, "标题", json.dumps(["封面·大字海报:纯文字封面 文字:「标题」"]), json.dumps(cc_text)).lastrowid
        w4._run_images(d20)
        r20 = q("SELECT images,status FROM drafts WHERE id=?", d20)[0]
        rep.check("只有封面且是纯文字版式:不调用 AI 生图", len(gen3) == 0 and len(ren3) == 1 and len(r20["images"]) == 1 and r20["images"][0].endswith(".png") and r20["status"] == "ready", f"{gen3} {ren3} {r20}")
        cc_photo = {"layout": "photo_top", "theme": "cream", "title": "标题", "scene": "窗边的桌面,有产品"}
        d21 = db.q_exec("INSERT INTO drafts(project_id,title,image_prompts,images,cover_card,status,with_images) VALUES(?,?,?,'[]',?,'queued',1)", ppj, "标题",
                        json.dumps(["封面·大图压字:实拍背景 窗边 文字:「标题」", "内页一,画面中不要出现任何文字", "内页二,画面中不要出现任何文字"]), json.dumps(cc_photo)).lastrowid
        gen3.clear(); ren3.clear()
        w4._run_images(d21)
        r21 = q("SELECT images,status,cover_card FROM drafts WHERE id=?", d21)[0]
        rep.check("封面 + 2 张内页:封面 1 张实拍背景 + 排版,内页 2 张 AI 图,都带产品参考图", len(gen3) == 3 and len(ren3) == 1 and all(c[1] for c in gen3) and r21["status"] == "ready"
                  and len(r21["images"]) == 3 and r21["cover_card"].get("photo", "").startswith(f"d{d21}_0_") and "场景:" in gen3[1][0] and "不要出现任何文字" in gen3[0][0], str(gen3)[:200])
        rep.check("封面的实拍图不带文字要求之外的字", "「" not in gen3[0][0])
        w4.submit_images = lambda did, redo_all=False: None
        before_layout = r21["cover_card"]["layout"]
        w4.change_cover(d21)
        r21b = q("SELECT images,cover_card,image_prompts FROM drafts WHERE id=?", d21)[0]
        rep.check("换封面:换了版式,只清空封面这一张,封面说明同步更新", r21b["cover_card"]["layout"] != before_layout and r21b["images"][0] == "" and all(r21b["images"][1:]) and r21b["image_prompts"][0].startswith("封面·"), str(r21b)[:200])
        w4.submit_images = o_sub3
        gen3.clear(); ren3.clear()
        w4._run_images(d21)
        rep.check("换封面后补图:有现成实拍图,不再花钱生成封面背景", len(gen3) == 0 and len(ren3) == 1, f"{len(gen3)} {len(ren3)}")
    finally:
        ig12.generate_image, cd2.render, ig12.IMG_DIR, w4.submit_images = o_gen3, o_render3, o_dir3, o_sub3
    # 笔记库沉淀任务
    from app import api_admin as aa16
    class _Rq16:
        def __init__(self, body=None): self._b = body; self.query_params = {}; self.client = None
        async def json(self): return self._b
    r16 = aa16.add_library_tasks(aa16.LibTasks(queries=["沉淀词甲,沉淀词乙\n沉淀词丙", "沉淀词甲"], label="美食", limit=12))
    rep.check("沉淀任务:逗号/换行拆成多个词,重复的去掉", r16["added"] == 3 and not r16["skipped"], str(r16))
    t16 = q("SELECT * FROM crawl_tasks WHERE purpose='library' ORDER BY id")
    rep.check("沉淀任务:不属于任何项目,带赛道标签、条数和时间范围", len(t16) == 3 and all(t["project_id"] is None and t["label"] == "美食" and t["lim"] == 12 and t["max_age_days"] == 180 for t in t16), str(t16[0]))
    r16b = aa16.add_library_tasks(aa16.LibTasks(queries=["沉淀词甲", "新词"], label="美食"))
    rep.check("沉淀任务:已在队列里的词不重复下发", r16b["added"] == 1 and r16b["skipped"][0]["query"] == "沉淀词甲", str(r16b))
    for bad16 in (aa16.LibTasks(queries=[" "]), aa16.LibTasks(type="x", queries=["a"])):
        try:
            aa16.add_library_tasks(bad16); rep.check("沉淀任务:空内容/类型不对拒绝", False)
        except Exception as ex:
            rep.check("沉淀任务:空内容/类型不对拒绝", getattr(ex, "status_code", 0) == 400)
    # 领取顺序:用户任务在前;可以暂停
    db.q_exec("DELETE FROM crawl_tasks WHERE type='title'")
    lib_t = aa16.add_library_tasks(aa16.LibTasks(type="title", queries=["沉淀标题"], label="美食"))
    pj_t = db.q_exec("INSERT INTO crawl_tasks(project_id,type,query,status) VALUES(?,?,?,'pending')", pid11, "title", "用户标题").lastrowid
    c16 = ac15.claim_tasks(_Rq16(), 1, "w16", "title")["tasks"]
    rep.check("领取顺序:用户任务排在沉淀任务前面", len(c16) == 1 and c16[0]["id"] == pj_t, str(c16))
    c16b = ac15.claim_tasks(_Rq16(), 1, "w16", "title")["tasks"]
    rep.check("用户任务领完后才轮到沉淀任务", len(c16b) == 1 and c16b[0]["query"] == "沉淀标题" and c16b[0]["skip_known"] is True, str(c16b))
    db.q_exec("UPDATE crawl_tasks SET status='pending' WHERE id=?", c16b[0]["id"])
    aa16.library_switch(aa16.LibSwitch(enabled=False))
    rep.check("暂停沉淀:采集端领不到沉淀任务", ac15.claim_tasks(_Rq16(), 5, "w16", "title")["tasks"] == [])
    aa16.library_switch(aa16.LibSwitch(enabled=True))
    c16c = ac15.claim_tasks(_Rq16(), 5, "w16", "title")["tasks"]
    rep.check("恢复后又能领到", len(c16c) == 1 and c16c[0]["query"] == "沉淀标题")
    # 回传:笔记只进笔记库,带赛道标签;统计新增
    res16 = _as15.run(ac15.receive_notes(_Rq16({"task_id": c16c[0]["id"], "notes": [
        {"note_id": "LIB1", "title": "沉淀的笔记一", "content": "x", "likes": 5}, {"note_id": "LIB2", "title": "沉淀的笔记二", "content": "y", "likes": 9}, {"note_id": "LL0", "title": "榴莲酥做法第0种", "content": "榴莲酥" * 5}], "done": True})))
    n16 = {r["uid"]: r for r in q("SELECT uid, track, keyword FROM notes WHERE uid IN ('LIB1','LIB2')")}
    tk16 = q("SELECT * FROM crawl_tasks WHERE id=?", c16c[0]["id"])[0]
    rep.check("沉淀任务回传:笔记进笔记库,带赛道标签和搜索词,不关联项目", n16["LIB1"]["track"] == "美食" and n16["LIB1"]["keyword"] == "沉淀标题"
              and q("SELECT COUNT(*) c FROM project_notes pn JOIN notes n ON n.id=pn.note_id WHERE n.uid IN ('LIB1','LIB2')")[0]["c"] == 0, str(n16))
    rep.check("沉淀任务统计:共看到 3 篇,其中 2 篇是库里没有的", tk16["status"] == "done" and tk16["note_count"] == 3 and tk16["new_count"] == 2, str(tk16))
    st16 = aa16.library_stats()
    rep.check("沉淀统计:累计新增、开关状态", st16["new_total"] == 2 and st16["new_7d"] == 2 and st16["enabled"] and st16["pending"] >= 1, str(st16))
    rep.check("任务列表:可按来源筛选,沉淀任务显示赛道标签", all(t["purpose"] == "library" for t in aa16.all_tasks(purpose="library")) and all((t["purpose"] or "project") == "project" for t in aa16.all_tasks(purpose="project"))
              and next(t for t in aa16.all_tasks(purpose="library") if t["id"] == tk16["id"])["track"] == "美食")
    ft16 = aa16.library_from_tracks(aa16.LibFromTracks(tracks=["美食"]))
    rep.check("按赛道预设词批量加入", ft16["added"] >= 3 and all(t["label"] == "美食" for t in q("SELECT label FROM crawl_tasks WHERE purpose='library' AND label='美食' LIMIT 3")), str(ft16)[:120])
    fp16 = aa16.library_from_projects(aa16.LibFromTracks(tracks=[]))
    rep.check("加入用户做过的关键词(已在队列里的跳过)", fp16["added"] >= 1 and any(x["query"] == "腐乳做法" for x in fp16["skipped"]) or fp16["added"] >= 1, str(fp16)[:160])
    cl16 = aa16.clear_library_pending()
    rep.check("清空排队中的沉淀任务,用户任务不受影响", cl16["n"] >= 1 and q("SELECT COUNT(*) c FROM crawl_tasks WHERE purpose='library' AND status='pending'")[0]["c"] == 0 and q("SELECT COUNT(*) c FROM crawl_tasks WHERE project_id IS NOT NULL")[0]["c"] > 0)
    # 补全赛道
    from app import llm as llm17
    db.save_settings({"auto_tag": "1", "deepseek_api_key": "test-key"})
    sn11([{"note_id": f"NT{i}", "title": t, "content": "x", "likes": 1} for i, t in enumerate(["减脂期喝咖啡", "手冲咖啡教程", "买了件风衣", "乱七八糟的东西"])], track="")
    sn11([{"note_id": "NTHAS", "title": "有赛道的", "content": "x", "likes": 1}], track="穿搭")
    db.q_exec("UPDATE notes SET ai_status='done' WHERE uid LIKE 'NT%'")
    ids17 = {r["uid"]: r["id"] for r in q("SELECT id, uid FROM notes WHERE uid LIKE 'NT%'")}
    o_chat17, seen17 = llm17.chat, []
    def fake_chat17(system, user, temperature=1.0):
        seen17.append(user)
        return {"items": [{"id": ids17["NT0"], "track": "健身减脂"}, {"id": ids17["NT1"], "track": "美食"}, {"id": ids17["NT2"], "track": "穿搭"}, {"id": ids17["NT3"], "track": "瞎编的赛道"}]}
    llm17.chat = fake_chat17
    try:
        go17 = w4._track_batch()
        go17b = w4._track_batch()
    finally:
        llm17.chat = o_chat17
    t17 = {r["uid"]: r["track"] for r in q("SELECT uid, track FROM notes WHERE uid LIKE 'NT%'")}
    rep.check("补赛道:按内容归到已有赛道,模型编的赛道记「未分类」", t17["NT0"] == "健身减脂" and t17["NT1"] == "美食" and t17["NT2"] == "穿搭" and t17["NT3"] == "未分类", str(t17))
    rep.check("补赛道:已有赛道的不动,处理完后不再空转", t17["NTHAS"] == "穿搭" and go17 is True and go17b is False and "NTHAS" not in "".join(seen17).replace("【", ""), str(seen17)[:80])
    rep.check("补赛道:提示词里只给可选赛道", "可选赛道:" in seen17[0] and "美食" in seen17[0])
    rep.check("管理端:统计没有赛道的笔记数", aa16.note_facets()["no_track"] == 0)
    # 关键词扩展 / 老笔记重试改按最新
    from app import llm as llm18
    o_chat18 = llm18.chat
    llm18.chat = lambda system, user, temperature=1.0: {"keywords": ["红腐乳做法", "腐乳怎么选", "已有的词", "红腐乳做法", "下饭小菜", "乳", "腐乳配粥", "豆腐乳测评避坑指南超长词汇"]}
    try:
        kx18 = llm18.expand_keywords("红腐乳", "美食", 30, ["已有的词"])
        kr18 = aa16.expand_kw(aa16.KwExpand(topic="红腐乳"))
    finally:
        llm18.chat = o_chat18
    rep.check("关键词扩展:去重、去掉已有的、不带核心词的、太短太长的", kx18 == ["红腐乳做法", "腐乳怎么选", "腐乳配粥"], str(kx18))
    rep.check("关键词扩展接口:返回词表,队列里已有的词不再给", "腐乳怎么选" in kr18["keywords"] and "skipped_existing" in kr18, str(kr18))
    try:
        aa16.expand_kw(aa16.KwExpand(topic=" ")); rep.check("关键词扩展:没填产品拒绝", False)
    except Exception as ex:
        rep.check("关键词扩展:没填产品拒绝", getattr(ex, "status_code", 0) == 400)
    rep.check("一键生成:关键词上限可调", len(au.refine_keywords("美食", ["a做法", "b做法", "c做法", "d做法", "e做法", "f做法", "g做法"], cap=6)) == 6 and len(au.refine_keywords("美食", ["a做法", "b做法", "c做法", "d做法", "e做法", "f做法"])) == 5)
    pj18 = db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('美食','[\"腐乳测评\"]','')").lastrowid
    tk18 = db.q_exec("INSERT INTO crawl_tasks(project_id,type,query,status,error,sort) VALUES(?,?,?,?,?,'popularity')", pj18, "keyword", "腐乳测评", "failed", "最近 180 天没有符合条件的笔记（16 篇超出时间范围）").lastrowid
    au.new_run(pj18, 3); au.tick()
    r18 = q("SELECT status, sort FROM crawl_tasks WHERE id=?", tk18)[0]
    rep.check("全被时间范围筛掉的任务:重试时改按最新排序", r18["status"] == "pending" and r18["sort"] == "latest", str(r18))
    au.cancel(pj18)
    # 采集端是否用上了「先问哪些已有」
    db.q_exec("INSERT OR IGNORE INTO collectors(worker,last_seen) VALUES('kw19', ?)", __import__("time").time())
    _as15.run(ac15.known_notes(_Rq16({"ids": ["LL0", "LL1", "没有的一篇"], "worker": "kw19"})))
    w19 = next(w for w in ac15.collector_summary()["workers"] if w["worker"] == "kw19")
    rep.check("采集端用上「先问哪些已有」:记录次数、查了几篇、几篇已有", w19["known_calls"] == 1 and w19["known_total"] == 3 and w19["known_hit"] == 2 and w19["known_ago"] is not None, str(w19))
    rep.check("没调用过的采集端显示未使用", next(w for w in [{"known_calls": 0}] if not w["known_calls"]) is not None and all("known_calls" in w for w in ac15.collector_summary()["workers"]))
    # 续租 / 没带名称的调用算到真正的采集端头上
    db.q_exec("INSERT OR REPLACE INTO collectors(worker,last_seen,claimed) VALUES('lease20', ?, 0)", __import__("time").time())
    db.q_exec("DELETE FROM collectors WHERE worker='default'")
    tl20 = db.q_exec("INSERT INTO crawl_tasks(project_id,type,query,status,worker,claimed_at) VALUES(NULL,'title','续租测试','running','lease20',datetime('now','-14 minutes'))").lastrowid
    ac15.ping(_Rq16(), "lease20")
    age20 = q("SELECT CAST((julianday('now') - julianday(claimed_at)) * 1440 AS INT) m FROM crawl_tasks WHERE id=?", tl20)[0]["m"]
    rep.check("心跳续租:采集端 ping 后,它正在跑的任务超时计时重新开始", age20 <= 1, str(age20))
    db.q_exec("UPDATE crawl_tasks SET claimed_at=datetime('now','-14 minutes') WHERE id=?", tl20)
    _as15.run(ac15.receive_notes(_Rq16({"task_id": tl20, "notes": [{"note_id": "LEASE1", "title": "续租笔记", "content": "x", "likes": 1}], "done": False})))
    rep.check("分批回传(done:false)也续租,任务仍是进行中", q("SELECT status, CAST((julianday('now') - julianday(claimed_at)) * 1440 AS INT) m FROM crawl_tasks WHERE id=?", tl20)[0] == {"status": "running", "m": 0}
              or q("SELECT status FROM crawl_tasks WHERE id=?", tl20)[0]["status"] == "running")
    db.q_exec("UPDATE crawl_tasks SET claimed_at=datetime('now','-20 minutes') WHERE id=?", tl20)
    ac15.claim_tasks(_Rq16(), 1, "other20", "keyword")  # 另一个采集端来领任务:真正超时的才会被退回
    rep.check("真正超时(20 分钟没动静)的任务还是会退回待领取", q("SELECT status FROM crawl_tasks WHERE id=?", tl20)[0]["status"] == "pending")
    _as15.run(ac15.known_notes(_Rq16({"ids": ["LL0", "x20"]})))
    ws20 = {w["worker"]: w for w in ac15.collector_summary()["workers"]}
    rep.check("没带名称的「查询已有笔记」算到最近活跃的采集端头上,不再冒出 default", "default" not in ws20 and any(w["known_calls"] >= 1 for w in ws20.values()), str(list(ws20)))
    # 采集端跳过的已有笔记:采集完成时补关联到项目
    pj20 = db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('美食','[\"榴莲酥做法\"]','')").lastrowid
    au.new_run(pj20, 2, "collecting")
    db.q_exec("INSERT INTO crawl_tasks(project_id,type,query,status) VALUES(?,?,?,'done')", pj20, "keyword", "榴莲酥做法")
    db.save_settings({"auto_tag": "0", "auto_min_notes": "3", "auto_library_min": "999"})
    au.tick()
    rep.check("采集端跳过已有笔记后,采集完成时把相关的已有笔记补关联到项目", q("SELECT COUNT(*) c FROM project_notes WHERE project_id=?", pj20)[0]["c"] >= 15, str(q("SELECT COUNT(*) c FROM project_notes WHERE project_id=?", pj20)[0]))
    au.cancel(pj20)

    for _k in ('a', 'aa16', 'ex', 'i', 'k', 'r', 't', 'x',):
        if _k in locals():
            setattr(env, _k, locals()[_k])
