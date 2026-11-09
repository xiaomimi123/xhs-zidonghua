"""配图补缺、项目编辑、复刻选题冲突、笔记库备份、笔记主题分类。"""
import json
import re
from app import db
from pathlib import Path


def run(rep, env):
    pid, q, user = env.pid, env.q, env.user
    print("\n[配图:位置对齐与只补缺失]")
    import tempfile as _tf
    from app import imagegen as ig, worker as wk
    orig_gen, orig_dir = ig.generate_image, ig.IMG_DIR
    ig.IMG_DIR = Path(_tf.mkdtemp())
    calls, fail_once = [], {"p1"}

    def fake_gen(prompt, name, s=None):
        calls.append(prompt)
        key = next((seg for seg in prompt.split("。") if re.fullmatch(r"p\d+", seg)), prompt)  # 提示词里的那条原始内容(前面可能有封面版式、主题)
        if key in fail_once:
            fail_once.discard(key)
            raise RuntimeError("连不上生图接口:ConnectError(已自动重试 3 次)")
        (ig.IMG_DIR / f"{name}.png").write_bytes(b"x")
        return f"{name}.png"

    ig.generate_image = fake_gen
    try:
        did = db.q_exec("INSERT INTO drafts(project_id,title,image_prompts,images,status,with_images) VALUES(?,?,?,?,?,1)",
                        pid, "姜黄咖啡", json.dumps(["p1", "p2", "p3"]), "[]", "queued").lastrowid
        wk._run_images(did)
        d = q("SELECT * FROM drafts WHERE id=?", did)[0]
        rep.check("某张失败时其余照常生成,位置不错位", d["images"][0] == "" and d["images"][1] and d["images"][2], str(d["images"]))
        rep.check("有失败:状态为 error 并提示补全", d["status"] == "error" and "补全缺失" in d["error"], d["error"])
        rep.check("提示词不再塞标题", all("姜黄咖啡" not in c and "不要标题大字" in c for c in calls), str(calls[:1]))
        calls.clear()
        wk._run_images(did)
        d = q("SELECT * FROM drafts WHERE id=?", did)[0]
        rep.check("再点「补全」只重画缺失的那张", len(calls) == 1 and "p1" in calls[0], str(calls))
        rep.check("补全后全部就绪", d["status"] == "ready" and all(d["images"]), f"{d['status']} {d['images']}")
        calls.clear()
        wk._run_images(did, redo_all=True)
        rep.check("「全部重画」才会重画所有图", len(calls) == 3, str(len(calls)))
        db.q_exec("UPDATE drafts SET images=? WHERE id=?", json.dumps(["only.png"]), did)  # 旧数据:长度对不上
        calls.clear()
        wk._run_images(did)
        rep.check("旧数据(长度对不上)按全部未生成处理", len(calls) == 3, str(len(calls)))
    finally:
        ig.generate_image, ig.IMG_DIR = orig_gen, orig_dir

    print("\n[项目编辑]")
    rr = user.put(f"/api/projects/{pid}", json={"keywords": ["腐乳，咖啡,黄姜粉"], "direction": "只写姜黄粉"})
    p_ = next(p for p in user.get("/api/projects").json() if p["id"] == pid)
    rep.check("编辑项目:关键词按逗号拆开,保存内容方向", rr.status_code == 200 and p_["keywords"] == ["腐乳", "咖啡", "黄姜粉"] and p_["direction"] == "只写姜黄粉", str(p_))
    rep.check("编辑项目:至少要一个关键词", user.put(f"/api/projects/{pid}", json={"keywords": ["  ,，"], "direction": ""}).status_code == 400)
    rep.check("编辑项目不会新建采集任务", not q("SELECT id FROM crawl_tasks WHERE project_id=? AND query IN ('腐乳','咖啡','黄姜粉')", pid))

    print("\n[复刻:选题冲突重写]")
    from app import worker as w4, llm as llm4
    from app.notes import save_notes
    db.save_settings({"auto_review": "0", "auto_fix": "0"})
    ra = save_notes([{"note_id": "K1", "title": "姜黄粉", "content": "腐乳蘸酱", "likes": 1}], project_id=pid)
    rb = save_notes([{"note_id": "K2", "title": "咖啡", "content": "冻干咖啡冲泡", "likes": 1}], project_id=pid)
    nids = [r["id"] for r in q("SELECT id FROM notes WHERE uid IN ('K1','K2')")]
    pbid = db.q_exec("INSERT INTO playbooks(project_id,name,note_ids,data,status) VALUES(?,?,?,?,'done')", pid, "测试", json.dumps(nids), json.dumps({"topics": ["咖啡"]})).lastrowid
    mk = lambda: db.q_exec("INSERT INTO drafts(project_id,playbook_id,angle,extra,status,with_images) VALUES(?,?,?,?,'queued',0)", pid, pbid, "主题:咖啡", "").lastrowid
    base = {"alt_titles": [], "tags": ["a"], "image_prompts": []}
    replies = []
    orig_w = llm4.write_note
    def fake_w(project, pb, refs, angle, extra="", kb="", banned=""):
        replies.append(extra)
        return {**base, **replies_data[min(len(replies), len(replies_data)) - 1]}
    llm4.write_note = fake_w
    try:
        replies_data = [{"title": "腐乳配咖啡", "content": "咖啡加腐乳一起吃"}, {"title": "咖啡的三种冲法", "content": "冻干咖啡冲泡"}]
        d1 = mk(); w4._run_text(d1)
        r1 = q("SELECT * FROM drafts WHERE id=?", d1)[0]
        rep.check("复刻写出强行组合:带反馈重写一次", len(replies) == 2 and "腐乳" in replies[1] and r1["title"] == "咖啡的三种冲法", f"{len(replies)} {r1['title']}")
        rep.check("重写后干净:不提示冲突", r1["checks"].get("combo") == [], str(r1["checks"])[:80])
        replies.clear(); replies_data = [{"title": "腐乳配咖啡", "content": "咖啡加腐乳一起吃"}]
        d2 = mk(); w4._run_text(d2)
        r2 = q("SELECT * FROM drafts WHERE id=?", d2)[0]
        rep.check("重写后仍然组合:只重试一次,并在页面上标出", len(replies) == 2 and r2["checks"].get("combo") == [["腐乳", "咖啡"]], f"{len(replies)} {str(r2['checks'])[:90]}")
    finally:
        llm4.write_note = orig_w

    print("\n[笔记库备份]")
    import tempfile as _t2, zipfile as _zf
    from app import archive as ar, mirror as mr
    from app.notes import parse_file as _pf, save_notes as _sn
    od_, om_ = ar.ARCHIVE, mr.DIR
    ar.ARCHIVE, mr.DIR = Path(_t2.mkdtemp()), Path(_t2.mkdtemp())
    try:
        (mr.DIR / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"1" * 20)
        _sn([{"note_id": "AR1", "title": "备份一", "content": "正文\n含换行,和逗号", "likes": "1.2万", "tags": ["a", "b"],
              "images": ["https://x/1.png", "https://x/2.png"], "ip_location": "广东", "author_url": "https://www.xiaohongshu.com/user/profile/u1"}],
            project_id=pid, keyword="k1", track="美食")
        _sn([{"note_id": "AR2", "title": "备份二", "content": "x", "likes": 5}], project_id=pid)
        db.q_exec("UPDATE notes SET images_local=? WHERE uid='AR1'", json.dumps(["a.png", ""]))
        ids = [r["id"] for r in q("SELECT id FROM notes WHERE uid IN ('AR1','AR2')")]
        info = ar.export_notes(ids)
        z = _zf.ZipFile(ar.ARCHIVE / info["name"])
        names = set(z.namelist())
        rep.check("备份包内容齐全", {"notes.json", "notes.csv", "README.txt", "images/AR1/1.png"} <= names and "images/AR1/2.png" not in names, str(sorted(names)))
        rep.check("备份统计", info["notes"] == 2 and info["images"] == 1, str(info))
        data = json.loads(z.read("notes.json"))
        a1 = next(n for n in data["notes"] if n["note_id"] == "AR1")
        rep.check("JSON 含完整数据和采集端原始字段", a1["likes"] == 12000 and a1["tags"] == ["a", "b"] and a1["raw"]["ip_location"] == "广东" and a1["keyword"] == "k1" and a1["projects"], str(a1)[:160])
        rep.check("图片:本地副本按相对路径记录,没转存成功的留空", a1["images_local"] == ["images/AR1/1.png", ""] and a1["images"] == ["https://x/1.png", "https://x/2.png"], str(a1["images_local"]))
        rows = _pf("notes.csv", z.read("notes.csv"))
        r1 = next(r for r in rows if r.get("title") == "备份一")
        rep.check("CSV 能被「导入」识别(含换行和逗号的正文)", len(rows) == 2 and r1["content"] == "正文\n含换行,和逗号" and r1["likes"] == "12000" and r1["author_id"] == "u1", str(r1))
        again = _sn(_pf("notes.json", z.read("notes.json")), project_id=pid)
        rep.check("JSON 重新导入:按笔记 ID 去重,不重复入库", again["new"] == 0 and again["updated"] == 2, str(again))
        raw_ = json.loads(q("SELECT raw FROM notes WHERE uid='AR1'")[0]["raw"])
        rep.check("重新导入后原始数据没被备份里的说明字段弄脏", raw_.get("ip_location") == "广东" and not ({"projects", "ai", "images_local", "raw"} & set(raw_)), str(sorted(raw_)))
        z2 = _zf.ZipFile(ar.ARCHIVE / ar.export_notes(ids, with_images=False)["name"])
        rep.check("不带图片时包里没有 images/", not any(n.startswith("images/") for n in z2.namelist()))
        rep.check("备份列表:同一秒连续导出两次不会互相覆盖", len(ar.list_archives()) == 2 and len({a_["name"] for a_ in ar.list_archives()}) == 2, str(ar.list_archives()))
    finally:
        ar.ARCHIVE, mr.DIR = od_, om_

    print("\n[笔记主题:入库后分类、迁移与筛选]")
    from app import worker as w6, llm as llm7, api_admin as adm
    db.save_settings({"auto_tag": "1", "deepseek_api_key": "k"})
    db.q_exec("UPDATE notes SET ai_status='done', topic='姜黄粉'")  # 先把已有的笔记都标成已分类,只留下面两篇待处理
    save_notes([{"note_id": "T1", "title": "自晒生姜粉", "content": "生姜粉做法", "likes": 1},
                {"note_id": "T2", "title": "速溶咖啡冲泡", "content": "咖啡", "likes": 1}],
               project_id=pid, keyword="腐乳,咖啡,黄姜粉", track="美食")
    seen_known = []

    seen_terms = []

    def fake_tag(rows, known=None, terms=None):
        seen_known.append(list(known or []))
        seen_terms.append(list(terms or []))
        return {r["id"]: {"type": "教程攻略", "topic": "生姜粉" if "生姜" in r["title"] else "速溶咖啡", "audience": "a", "highlight": "h", "tags": ["t"]} for r in rows}
    o_tag = llm7.tag_notes
    llm7.tag_notes = fake_tag
    try:
        first, second = w6._tag_batch(), w6._tag_batch()
        t1, t2 = q("SELECT * FROM notes WHERE uid='T1'")[0], q("SELECT * FROM notes WHERE uid='T2'")[0]
        rep.check("入库后按内容分到各自的主题", t1["topic"] == "生姜粉" and t2["topic"] == "速溶咖啡" and t1["ai_status"] == "done", f"{t1['topic']} {t2['topic']}")
        rep.check("搜索词保持原样,不被主题覆盖", t1["keyword"] == "腐乳,咖啡,黄姜粉")
        rep.check("处理完一批后返回 True,没有待办时返回 False", first is True and second is False)
        rep.check("已有主题带给模型", "姜黄粉" in seen_known[0], str(seen_known[0]))
        rep.check("项目的搜索词带给模型", {"腐乳", "咖啡", "黄姜粉"} <= set(seen_terms[0]), str(seen_terms[0]))
        fa = adm.note_facets("美食")
        rep.check("筛选项:按主题列出各自的数量", {x["k"]: x["n"] for x in fa["topics"]}.get("生姜粉") == 1, str(fa["topics"]))
        w_, a_ = adm._note_filter(topic="生姜粉")
        rep.check("按主题筛选", db.q_one(f"SELECT COUNT(*) n FROM notes n WHERE {w_}", *a_)["n"] == 1)
        adm.edit_note(t1["id"], adm.NoteEdit(track="美食", keyword=t1["keyword"], ai_type="教程攻略", topic="姜黄粉"))
        rep.check("手动改主题", q("SELECT topic FROM notes WHERE id=?", t1["id"])[0]["topic"] == "姜黄粉")
        adm.edit_note(t1["id"], adm.NoteEdit(track="美食", keyword=t1["keyword"], ai_type="教程攻略"))
        rep.check("编辑时不填主题不会把已有主题清空", q("SELECT topic FROM notes WHERE id=?", t1["id"])[0]["topic"] == "姜黄粉")
        db.q_exec("UPDATE notes SET topic='', ai_status='done' WHERE id=?", t2["id"])
        db.init()
        rep.check("升级迁移:有标签但没有主题的笔记重新排队补主题", q("SELECT ai_status FROM notes WHERE id=?", t2["id"])[0]["ai_status"] == "pending"
                  and q("SELECT ai_status FROM notes WHERE id=?", t1["id"])[0]["ai_status"] == "done")

        def boom(rows, known=None, terms=None):
            raise RuntimeError("DeepSeek 返回 401:Key 无效")
        llm7.tag_notes = boom
        rep.check("Key 有问题:标记出错并停止,不空转", w6._tag_batch() is False and q("SELECT ai_status FROM notes WHERE id=?", t2["id"])[0]["ai_status"] == "error")
    finally:
        llm7.tag_notes = o_tag

    for _k in ('adm', 'banned', 'c', 'calls', 'd', 'did', 'extra', 'fake_gen', 'fake_tag', 'mk', 'n', 'name', 'o_tag', 'p', 'prompt', 'r', 'r1', 'r2', 'refs', 't1', 't2', 'w4', 'x', 'z2',):
        if _k in locals():
            setattr(env, _k, locals()[_k])
