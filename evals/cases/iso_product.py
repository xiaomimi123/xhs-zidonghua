"""产品图入口:产品信息进文案、产品参考图、识别产品、看图接口、重试与补词、流水线接口、编辑产品。"""
import json
from app import db
from pathlib import Path


def run(rep, env):
    au, pid11, pid13, q, sn11, today11, w4 = env.au, env.pid11, env.pid13, env.q, env.sn11, env.today11, env.w4
    print("\n[产品图入口]")
    import base64 as _b64, tempfile as _tf12, shutil as _sh12
    from app import products as pr12, llm as llm12, imagegen as ig12
    PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    o_dir12 = pr12.DIR
    pr12.DIR = Path(_tf12.mkdtemp())
    def bad12(urls):
        try:
            pr12.decode_images(urls); return False
        except ValueError:
            return True
    rep.check("产品图校验:合法图片通过", len(pr12.decode_images([PNG])) == 1)
    rep.check("产品图校验:拒绝空、非图片、SVG、坏数据、太多张", bad12([]) and bad12(["hello"]) and bad12(["data:image/svg+xml;base64," + _b64.b64encode(b"<svg onload=1>").decode()])
              and bad12(["data:image/png;base64,@@@"]) and bad12([PNG] * 10))
    ppid = pr12.create("测试腐乳", [PNG, PNG], price="29.9元", selling_points="古法发酵", forbid="不写疗效")
    prod12 = pr12.get(ppid)
    rep.check("产品入库:图片落盘", len(prod12["images"]) == 2 and all(f.is_file() for f in pr12.paths(prod12)))
    blk = pr12.block(prod12)
    rep.check("产品资料块:带名称/卖点/价格/禁用说法,要求自然植入", all(x in blk for x in ("测试腐乳", "古法发酵", "29.9元", "不写疗效", "自然植入", "【产品资料】")))
    try:
        pr12.create("   ", [PNG]); rep.check("没填产品名拒绝", False)
    except ValueError:
        rep.check("没填产品名拒绝", True)
    sse = ': PING\n\ndata: {"choices":[{"delta":{"content":"你"}}]}\n\n: PING\n\ndata: {"choices":[{"delta":{"content":"好"}}]}\n\ndata: [DONE]\n'
    rep.check("看图返回:兼容流式(含保活行)和普通 JSON", llm12.parse_chat_text(sse) == "你好" and llm12.parse_chat_text('{"choices":[{"message":{"content":"ok"}}]}') == "ok" and llm12.parse_chat_text("") == "")
    ov12 = llm12.vision_chat
    llm12.vision_chat = lambda system, text, images, model=None: '好的 {"category":"腐乳","track":"不存在的赛道","appearance":"红色玻璃罐","selling_points":["a","b","c","d","e"],"keywords":["腐乳做法","x"]} 完'
    try:
        dsc = llm12.describe_product("测试腐乳", [], ["美食", "穿搭"])
    finally:
        llm12.vision_chat = ov12
    rep.check("产品识别:规范化(赛道不在列表里清空、数量限制、过短的词去掉)", dsc["category"] == "腐乳" and dsc["track"] == "" and len(dsc["selling_points"]) == 4 and dsc["keywords"] == ["腐乳做法"], str(dsc))

    # 产品信息进入文案
    got12 = {}
    o_wn, o_pa = llm12.write_note, llm12.plan_angles
    llm12.write_note = lambda project, pb, refs, angle, extra="", kb="", banned="": got12.update(extra=extra, banned=banned) or {"title": "t", "content": "正文", "alt_titles": [], "tags": ["a"], "image_prompts": []}
    llm12.plan_angles = lambda project, pb, n, extra="": got12.update(plan=extra) or ["角度"] * n
    db.save_settings({"auto_review": "0", "auto_fix": "0", "auto_compress": "0"})
    ppj = db.q_exec("INSERT INTO projects(track,keywords,direction,product_id) VALUES('美食','[\"腐乳做法\"]','',?)", ppid).lastrowid
    nids12 = [r["id"] for r in q("SELECT id FROM notes WHERE uid LIKE 'AU%'")][:2]
    pb12 = db.q_exec("INSERT INTO playbooks(project_id,name,note_ids,data,status) VALUES(?,?,?,?,'done')", ppj, "t", json.dumps(nids12), "{}").lastrowid
    d12 = db.q_exec("INSERT INTO drafts(project_id,playbook_id,angle,extra,status,with_images) VALUES(?,?,?,?,'queued',0)", ppj, pb12, "角度", "").lastrowid
    o_sub = w4.submit_text
    w4.submit_text = lambda did: None
    try:
        w4._plan(ppj, pb12, [d12], "")
        w4._run_text(d12)
    finally:
        llm12.write_note, llm12.plan_angles, w4.submit_text = o_wn, o_pa, o_sub
    rep.check("产品资料带进角度规划", "【产品资料】" in got12.get("plan", "") and "测试腐乳" in got12.get("plan", ""), got12.get("plan", "")[:60])
    rep.check("产品资料带进写作,禁用说法进禁用词", "古法发酵" in got12.get("extra", "") and "不写疗效" in got12.get("banned", ""), got12.get("extra", "")[:60])

    # 配图:产品参考图 + 原图直出
    rep.check("配图提示词:有产品时要求保持产品外观", w4._image_prompt("标题", "场景", True).startswith("以参考图中的产品为主体") and not w4._image_prompt("标题", "场景").startswith("以参考图"))
    o_dir12i, o_gen = ig12.IMG_DIR, ig12.generate_image
    ig12.IMG_DIR = Path(_tf12.mkdtemp())
    calls12 = []
    def fake_gen(prompt, name, s=None, refs=None):
        calls12.append((prompt, refs)); (ig12.IMG_DIR / f"{name}.png").write_bytes(b"x"); return f"{name}.png"
    ig12.generate_image = fake_gen
    db.save_settings({"product_real_slots": "1", "product_max_refs": "2"})
    d13 = db.q_exec("INSERT INTO drafts(project_id,playbook_id,title,image_prompts,images,status,with_images) VALUES(?,?,?,?,'[]','queued',1)", ppj, pb12, "标题", json.dumps(["封面", "内页1", "内页2", "内页3"])).lastrowid
    try:
        w4._run_images(d13)
    finally:
        ig12.generate_image, ig12.IMG_DIR = o_gen, o_dir12i
    r13 = q("SELECT images,status FROM drafts WHERE id=?", d13)[0]
    rep.check("配图:4 张,最后 1 张直接用产品原图,其余 3 张 AI 生成", len(r13["images"]) == 4 and all(r13["images"]) and r13["images"][3].startswith(f"d{d13}_4_") and len(calls12) == 3 and r13["status"] == "ready", f"{r13} {len(calls12)}")
    rep.check("配图:AI 生成的带产品参考图(最多 2 张)", all(c[1] and len(c[1]) == 2 for c in calls12) and calls12[0][0].startswith("以参考图中的产品"))

    # 一键生成:先识别产品
    o_desc, o_pool, o_ks = llm12.describe_product, w4._pool, au.kw_suggest
    class Sync:
        def submit(self, fn, *a): fn(*a)
    w4._pool = lambda kind: Sync()
    au.kw_suggest = lambda track, w: {"broad": False, "suggestions": []}
    try:
        llm12.describe_product = lambda name, images, tracks: {"category": "腐乳", "track": "美食", "keywords": ["腐乳做法", "腐乳怎么吃"], "appearance": "红色玻璃罐"}
        pj1 = db.q_exec("INSERT INTO projects(track,keywords,direction,product_id) VALUES('','[]','',?)", ppid).lastrowid
        au.new_run(pj1, 3, "profiling")
        au.tick()
        rp = q("SELECT * FROM projects WHERE id=?", pj1)[0]
        rep.check("识别产品:定赛道、搜索词、内容方向,并建采集任务", rp["track"] == "美食" and rp["keywords"] == ["腐乳做法", "腐乳怎么吃"] and "测试腐乳" in rp["direction"]
                  and q("SELECT COUNT(*) c FROM crawl_tasks WHERE project_id=?", pj1)[0]["c"] == 2 and q("SELECT status FROM autoruns WHERE project_id=?", pj1)[0]["status"] == "collecting", str(rp))
        rep.check("识别结果存回产品", pr12.get(ppid)["category"] == "腐乳")
        llm12.describe_product = lambda name, images, tracks: (_ for _ in ()).throw(RuntimeError("看图失败"))
        pj2 = db.q_exec("INSERT INTO projects(track,keywords,direction,product_id) VALUES('','[]','',?)", ppid).lastrowid
        au.new_run(pj2, 3, "profiling")
        au.tick()
        r2p = q("SELECT * FROM projects WHERE id=?", pj2)[0]
        rep.check("识别失败不卡住:改用产品名搜索", r2p["track"] == "测试腐乳" and r2p["keywords"] == ["测试腐乳"] and q("SELECT status FROM autoruns WHERE project_id=?", pj2)[0]["status"] == "collecting", str(r2p))
        rep.check("进度接口:有产品的项目带 product 标记", au.progress(pj1)["product"] is True and au.progress(pid11)["product"] is False)
    finally:
        llm12.describe_product, w4._pool, au.kw_suggest = o_desc, o_pool, o_ks
    # 看图接口:优先用自己的地址/Key,没填就沿用生图接口
    import httpx as _hx12
    seen12 = []
    class _R:
        status_code = 200
        text = '{"choices":[{"message":{"content":"红色"}}]}'
    o_post12 = _hx12.post
    _hx12.post = lambda url, **k: seen12.append((url, k["headers"]["Authorization"], k["json"]["model"])) or _R()
    pf12 = pr12.paths(pr12.get(ppid))[0]
    try:
        a12 = llm12.vision_chat("s", "t", [pf12], s={"vision_base_url": "https://q.example/v1/", "vision_api_key": "QK", "vision_model": "qwen-vl-max", "image_base_url": "https://i.example/v1", "image_api_key": "IK"})
        b12 = llm12.vision_chat("s", "t", [pf12], s={"vision_base_url": "", "vision_api_key": "", "vision_model": "m2", "image_base_url": "https://i.example/v1", "image_api_key": "IK"})
    finally:
        _hx12.post = o_post12
    rep.check("看图接口:填了千问地址就用千问的地址、Key 和模型", seen12[0] == ("https://q.example/v1/chat/completions", "Bearer QK", "qwen-vl-max") and a12 == "红色", str(seen12))
    rep.check("看图接口:留空时沿用生图接口", seen12[1] == ("https://i.example/v1/chat/completions", "Bearer IK", "m2"), str(seen12))
    try:
        llm12.vision_chat("s", "t", [pf12], s={})
        rep.check("看图接口:没配置时给出明确提示", False)
    except RuntimeError as ex:
        rep.check("看图接口:没配置时给出明确提示", "看图接口" in str(ex))
    # 搜索词跑偏 / 相关笔记太少 / 采集任务失败
    rep.check("搜索词要带品类词", au.on_topic("腐乳做法", "红腐乳") and au.on_topic("红腐乳怎么吃", "腐乳") and not au.on_topic("下饭小菜做法", "红腐乳") and au.on_topic("任何词", ""))
    o_desc2, o_pool2, o_ks2 = llm12.describe_product, w4._pool, au.kw_suggest
    w4._pool = lambda kind: Sync()
    au.kw_suggest = lambda track, w: {"broad": False, "suggestions": []}
    try:
        llm12.describe_product = lambda name, images, tracks: {"category": "红腐乳", "track": "美食", "keywords": ["下饭小菜做法", "腐乳推荐", "红腐乳怎么吃"]}
        pj3 = db.q_exec("INSERT INTO projects(track,keywords,direction,product_id) VALUES('','[]','',?)", ppid).lastrowid
        au.new_run(pj3, 3, "profiling"); au.tick()
        kw3 = q("SELECT keywords FROM projects WHERE id=?", pj3)[0]["keywords"]
        rep.check("识别出的搜索词:不带品类词的被去掉", kw3 == ["腐乳推荐", "红腐乳怎么吃"], str(kw3))
        llm12.describe_product = lambda name, images, tracks: {"category": "红腐乳", "track": "美食", "keywords": ["下饭小菜做法"]}
        pj4 = db.q_exec("INSERT INTO projects(track,keywords,direction,product_id) VALUES('','[]','',?)", ppid).lastrowid
        au.new_run(pj4, 3, "profiling"); au.tick()
        rep.check("搜索词全跑偏:改用「品类+做法/怎么吃」", q("SELECT keywords FROM projects WHERE id=?", pj4)[0]["keywords"] == ["红腐乳做法", "红腐乳怎么选"])
    finally:
        llm12.describe_product, w4._pool, au.kw_suggest = o_desc2, o_pool2, o_ks2
    # 失败的采集任务自动重试一次
    pj5 = db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('美食','[\"腐乳做法\"]','')").lastrowid
    tk5 = db.q_exec("INSERT INTO crawl_tasks(project_id,type,query,status,error) VALUES(?,?,?,?,?)", pj5, "keyword", "腐乳做法", "failed", "连续多篇笔记读取失败").lastrowid
    au.new_run(pj5, 3); au.tick()
    rep.check("采集任务失败:自动重试一次", q("SELECT status FROM crawl_tasks WHERE id=?", tk5)[0]["status"] == "pending" and q("SELECT state FROM autoruns WHERE project_id=?", pj5)[0]["state"]["retried"] == [tk5])
    db.q_exec("UPDATE crawl_tasks SET status='failed', error='又失败了' WHERE id=?", tk5)
    au.tick()
    rep.check("采集任务第二次失败:不再重试", q("SELECT status FROM crawl_tasks WHERE id=?", tk5)[0]["status"] == "failed")
    # 采回来的笔记大多不相关:补搜索词一轮;补完还是不相关就停下
    db.save_settings({"auto_tag": "0", "auto_min_notes": "3", "auto_min_relevant": "5"})
    pj6 = db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('美食','[\"火龙果做法\"]','')").lastrowid
    sn11([{"note_id": f"OFF{i}", "title": f"青椒炒蛋{i}", "content": "家常菜", "likes": 10 + i, "published_at": today11} for i in range(6)], project_id=pj6, keyword="火龙果做法", track="美食")
    db.q_exec("INSERT INTO crawl_tasks(project_id,type,query,status) VALUES(?,?,?,'done')", pj6, "keyword", "火龙果做法")
    au.new_run(pj6, 3)
    au.kw_suggest = lambda track, w: {"broad": True, "suggestions": [{"word": "火龙果怎么吃"}, {"word": "火龙果做法"}, {"word": "火龙果拌面"}]}
    from app import llm as _llm26
    _o_adj26, _llm26.expand_adjacent = _llm26.expand_adjacent, lambda *a, **k: []
    try:
        au.tick(); au.tick()  # collecting → tagging → 发现相关笔记太少
        r6 = q("SELECT * FROM autoruns WHERE project_id=?", pj6)[0]
        kw6 = q("SELECT keywords FROM projects WHERE id=?", pj6)[0]["keywords"]
        rep.check("相关笔记太少:补充搜索词并回到采集", r6["status"] == "collecting" and kw6 == ["火龙果做法", "火龙果怎么吃", "火龙果拌面"] and "补充搜索" in r6["msg"], f"{r6['status']} {kw6} {r6['msg']}")
        db.q_exec("UPDATE crawl_tasks SET status='done' WHERE project_id=?", pj6)
        au.tick(); au.tick()
        r6 = q("SELECT * FROM autoruns WHERE project_id=?", pj6)[0]
        rep.check("补充后仍然不相关:停下并说明", r6["status"] == "error" and "基本不相关" in r6["msg"], f"{r6['status']} {r6['msg']}")
    finally:
        au.kw_suggest = o_ks2
    rep.check("采集端在线窗口放宽到 150 秒,回传笔记也算心跳", __import__("app.api_collector", fromlist=["x"]).ONLINE_SEC == 150)
    # 流水线界面接口
    import zipfile as _zf13, io as _io13
    from app import api_user as au13
    rs13 = au13.list_runs()
    r11 = next(r for r in rs13 if r["id"] == pid11)
    rep.check("任务列表:完成的任务阶段条走满,带合格数", r11["status"] == "done" and r11["step"] == len(r11["steps"]) and r11["steps"][0] == "采集" and r11["drafts"]["passed"] == 3, str(r11)[:160])
    rp1 = au13.run_detail(pj1)
    rep.check("任务详情:产品任务第一步是识别产品,带品类", rp1["steps"][0] == "识别产品" and rp1["kind"] == "product" and rp1["name"] == "测试腐乳" and "腐乳" in rp1["category"], str(rp1)[:160])
    r13 = next(r for r in rs13 if r["id"] == pid13)
    rep.check("任务列表:取消的任务标出停在哪一步", r13["status"] == "canceled" and r13["steps"][r13["step"]] == "采集", str(r13)[:160])
    lib13 = au13.library(pid11)
    rep.check("成品库:按任务列出写好的笔记,带合格标记和任务名", len(lib13) == 4 and all(x["run"] == "美食 · 腐乳做法" for x in lib13) and sorted(x["auto_ok"] for x in lib13) == [0, 1, 1, 1], str([(x["run"], x["auto_ok"]) for x in lib13]))
    did13 = lib13[0]["id"]
    au13.set_published(did13, au13.PubIn(on=True))
    rep.check("标记已发布", au13.library(pid11)[0]["published"] is True and q("SELECT published_at FROM drafts WHERE id=?", did13)[0]["published_at"] != "")
    au13.set_published(did13, au13.PubIn(on=False))
    rep.check("取消已发布", au13.library(pid11)[0]["published"] is False)
    o_ig13 = ig12.IMG_DIR
    ig12.IMG_DIR = Path(_tf12.mkdtemp())
    for n13 in q("SELECT images FROM drafts WHERE id=?", d13)[0]["images"]:
        (ig12.IMG_DIR / n13).write_bytes(b"x")
    try:
        rz = au13.draft_images_zip(d13)
    finally:
        ig12.IMG_DIR = o_ig13
    names13 = _zf13.ZipFile(_io13.BytesIO(rz.body)).namelist()
    rep.check("单篇配图打包下载:按顺序编号", len(names13) == 4 and names13[0].startswith("1.") and "attachment" in rz.headers["content-disposition"], str(names13))
    try:
        au13.draft_images_zip(did13); rep.check("没有配图时提示", False)
    except Exception as ex:
        rep.check("没有配图时提示", getattr(ex, "status_code", 0) == 404)
    # 编辑产品
    before13 = pr12.get(ppid)["images"]
    pr12.update(ppid, "测试腐乳二代", "19.9元", "小罐装", "上班族", "不写疗效", keep=[before13[0]], add=[PNG])
    p13 = pr12.get(ppid)
    rep.check("编辑产品:改资料,去掉一张、加一张图,去掉的文件删除", p13["name"] == "测试腐乳二代" and p13["price"] == "19.9元" and len(p13["images"]) == 2
              and p13["images"][0] == before13[0] and not (pr12.DIR / before13[1]).exists() and (pr12.DIR / p13["images"][1]).is_file() and p13["profile"] == {}, str(p13)[:160])
    for bad13 in (dict(name=" ", keep=[before13[0]]), dict(name="x", keep=[]), dict(name="x", keep=[before13[0]], add=[PNG] * 9)):
        try:
            pr12.update(ppid, bad13["name"], keep=bad13["keep"], add=bad13.get("add")); rep.check("编辑产品:非法输入拒绝", False, str(bad13)[:40])
        except ValueError:
            pass
    rep.check("编辑产品:没名字、没图、超过 9 张都拒绝", True)
    pjb = db.q_exec("INSERT INTO projects(track,keywords,direction,product_id) VALUES('美食','[]','',?)", ppid).lastrowid
    db.q_exec("INSERT INTO autoruns(project_id,target,status,state) VALUES(?,?,?,?)", pjb, 3, "writing", "{}")
    try:
        au13.del_product(ppid); rep.check("删除产品:有任务正在用时拒绝", False)
    except Exception as ex:
        rep.check("删除产品:有任务正在用时拒绝", getattr(ex, "status_code", 0) == 400 and pr12.get(ppid) is not None)
    db.q_exec("UPDATE autoruns SET status='done' WHERE project_id=?", pjb)

    for _k in ('Sync', '_', '_o_adj26', '_tf12', 'a', 'au13', 'c', 'd12', 'ex', 'got12', 'i', 'ig12', 'k', 'n', 'name', 'o_dir12', 'pj1', 'ppid', 'ppj', 'pr12', 'prompt', 'r', 'refs', 'self', 'w', 'x',):
        if _k in locals():
            setattr(env, _k, locals()[_k])
