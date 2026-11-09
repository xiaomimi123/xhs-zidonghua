"""不依赖数据库的检查:生图适配与重试(模拟服务器)、配图提示词、选题与封面。"""
import json
from ..common import _raises2
from pathlib import Path


def run(rep):
    from app import compliance
    print("\n[生图适配(模拟服务器)]")
    import base64, tempfile
    import httpx
    from app import imagegen
    PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40
    CDN = "http://93.184.216.34/out.png"
    seen = {"posts": [], "cdn_auth": None, "polls": 0}

    def handler(req: httpx.Request):
        if req.url.path.endswith("/images/generations"):
            body = json.loads(req.content)
            seen["posts"].append(body)
            if body["prompt"] == "sync":
                return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
            if body["prompt"] == "denied":
                return httpx.Response(401, json={"error": {"message": "invalid key"}})
            if body["prompt"] == "nobalance":
                return httpx.Response(402, json={"error": {"message": "insufficient"}})
            return httpx.Response(200, json={"code": 200, "data": [{"status": "submitted", "task_id": "t1"}]})
        if req.url.path.endswith("/tasks/t1"):
            seen["polls"] += 1
            if seen["polls"] < 2:
                return httpx.Response(200, json={"code": 200, "data": {"status": "processing", "progress": 30}})
            return httpx.Response(200, json={"code": 200, "data": {"status": "completed", "result": {"images": [{"url": [CDN]}]}}})
        if str(req.url) == CDN:
            seen["cdn_auth"] = req.headers.get("authorization")
            return httpx.Response(200, content=PNG)
        return httpx.Response(404)

    orig_client, orig_dir, orig_every = httpx.Client, imagegen.IMG_DIR, imagegen.POLL_EVERY
    httpx.Client = lambda **kw: orig_client(transport=httpx.MockTransport(handler), **kw)
    imagegen.IMG_DIR, imagegen.POLL_EVERY = Path(tempfile.mkdtemp()), 0
    cfg = {"image_base_url": "https://api.example.test/v1", "image_api_key": "SECRET", "image_model": "m1", "image_size": "9:16", "image_resolution": "2k"}
    try:
        fn = imagegen.generate_image("async", "a1", cfg)
        rep.check("异步:提交→轮询→下载,保存成真实格式", fn == "a1.png" and (imagegen.IMG_DIR / fn).read_bytes() == PNG, fn)
        rep.check("异步:size / resolution / model 原样传给服务商", seen["posts"][0] == {"prompt": "async", "n": 1, "model": "m1", "size": "9:16", "resolution": "2k"}, str(seen["posts"][0]))
        rep.check("异步:API Key 不会发给图片服务器", seen["cdn_auth"] is None, str(seen["cdn_auth"]))
        rep.check("同步:直接返回 b64_json 也能处理", imagegen.generate_image("sync", "a2", {**cfg, "image_resolution": ""}) == "a2.png")
        rep.check("清晰度留空时不传 resolution", "resolution" not in seen["posts"][-1], str(seen["posts"][-1]))
        for prompt, word in [("denied", "Key 不正确"), ("nobalance", "余额不足")]:
            try:
                imagegen.generate_image(prompt, "x", cfg)
                rep.check(f"错误提示:{word}", False, "没有报错")
            except RuntimeError as e:
                rep.check(f"错误提示:{word}", word in str(e), str(e))
        try:
            imagegen.generate_image("x", "x", {**cfg, "image_api_key": ""})
            rep.check("没填 Key 时提示去配置", False)
        except RuntimeError as e:
            rep.check("没填 Key 时提示去配置", "接口配置" in str(e))
    finally:
        httpx.Client, imagegen.IMG_DIR, imagegen.POLL_EVERY = orig_client, orig_dir, orig_every

    print("\n[配图提示词与文字扫描]")
    from app import worker as _worker
    claims = lambda t: len(compliance.scan("", t, [])["confirmed"])
    for text, n in [("我的反应：前调是豆香", 1), ("直接买了三样回来试😅", 1), ("盲测排名放这里", 1), ("建议你自己这样测", 0), ("我的经验是每层都薄", 1), ("我自己的吃法是早餐配四分之一块", 1)]:
        rep.check(f"经历描述自动确认:{text[:14]}", claims(text) == n, f"期望 {n} 得到 {claims(text)}")
    rep.check("提取图上的文字", compliance.image_texts(["封面,大字「抗炎早餐」和“3分钟”", "平铺图"]) == ["抗炎早餐", "3分钟"])
    r = compliance.scan("", "", [], ["封面:大字「最好的姜黄咖啡」留白", "平铺图,画面中不要出现任何文字"])
    rep.check("图上的文字也过合规词库", any(h["word"] == "最好" and h["field"] == "image_text" for h in r["hits"]), str(r["hits"]))
    rep.check("没有图上文字时不误报", not compliance.scan("", "", [], ["平铺图,画面中不要出现任何文字"])["hits"])
    with_text, without = _worker._image_prompt("姜黄咖啡", "封面,大字「3分钟抗炎早餐」"), _worker._image_prompt("姜黄咖啡", "平铺图。")
    rep.check("提示词加固:不把标题塞进提示词,并禁止标题字/图标", "姜黄咖啡" not in without and "不要标题大字" in without and "主题:" not in without)
    rep.check("提示词加固:有文字时限定只能出现引号里的字", "只能是引号里" in with_text and "不要出现任何文字" not in with_text)
    rep.check("提示词加固:没文字时禁止出字", "不要出现任何文字" in without and "。。" not in without, without)

    print("\n[生图重试(模拟服务器)]")
    import base64 as _b64
    import httpx as _httpx
    from app import imagegen as _ig
    PNG2 = b"\x89PNG\r\n\x1a\n" + b"1" * 30
    posts = []
    plan = []  # 每次 POST 的行为,按顺序消费

    def h2(req):
        if req.url.path.endswith("/images/generations"):
            posts.append(1)
            act = plan.pop(0) if plan else "ok"
            if act == "conn":
                raise _httpx.ConnectError("boom", request=req)
            if act in (429, 503, 401):
                return _httpx.Response(act, json={"error": {"message": "x"}})
            if act == "async":
                return _httpx.Response(200, json={"data": [{"task_id": "T9"}]})
            return _httpx.Response(200, json={"data": [{"b64_json": _b64.b64encode(PNG2).decode()}]})
        if req.url.path.endswith("/tasks/T9"):
            if plan and plan[0] == "poll-conn":
                plan.pop(0)
                raise _httpx.ConnectError("boom", request=req)
            return _httpx.Response(200, json={"data": {"status": "completed", "result": {"images": [{"url": ["http://93.184.216.34/z.png"]}]}}})
        return _httpx.Response(200, content=PNG2)

    oc, od, ow, op = _httpx.Client, _ig.IMG_DIR, _ig.RETRY_WAITS, _ig.POLL_EVERY
    _httpx.Client = lambda **kw: oc(transport=_httpx.MockTransport(h2), **kw)
    _ig.IMG_DIR, _ig.RETRY_WAITS, _ig.POLL_EVERY = Path(tempfile.mkdtemp()), (0, 0, 0), 0
    cfg2 = {"image_base_url": "https://api.example.test/v1", "image_api_key": "K", "image_model": "m"}
    try:
        def run(actions):
            posts.clear(); plan[:] = actions
            try:
                return _ig.generate_image("p", "t", cfg2), len(posts), ""
            except RuntimeError as e:
                return None, len(posts), str(e)
        fn, n, err = run(["conn", "ok"])
        rep.check("连接错误:自动重试后成功", fn == "t.png" and n == 2, f"{fn} 请求 {n} 次 {err}")
        fn, n, err = run([429, 503, "ok"])
        rep.check("限流/服务暂不可用:自动重试", fn == "t.png" and n == 3, f"{fn} 请求 {n} 次 {err}")
        fn, n, err = run(["conn", "conn", "conn"])
        rep.check("一直失败:试 3 次后报错并说明", fn is None and n == 3 and "已自动重试 3 次" in err, f"请求 {n} 次 {err}")
        fn, n, err = run([401])
        rep.check("Key 错误:不重试", fn is None and n == 1 and "Key 不正确" in err, f"请求 {n} 次 {err}")
        fn, n, err = run(["async", "poll-conn"])
        rep.check("异步:查询状态时断网不会重新提交(防重复扣费)", fn == "t.png" and n == 1, f"{fn} 提交 {n} 次 {err}")
    finally:
        _httpx.Client, _ig.IMG_DIR, _ig.RETRY_WAITS, _ig.POLL_EVERY = oc, od, ow, op

    print("\n[选题与封面]")
    from app import worker as w2, llm as llm2
    import random as _rd2, shutil as _sh2, tempfile as _tf2
    from app import covers as cv2, cards as cd2
    rep.check("封面:12 种版式、6 套配色,实拍/纯文字各 6 种", len(cd2.LAYOUTS) == 12 and len(cd2.THEMES) == 6 and set(cv2.TEXT_LAYOUTS + cv2.PHOTO_LAYOUTS) == set(cd2.LAYOUTS)
              and all(cd2.LAYOUTS[k][1] for k in cv2.PHOTO_LAYOUTS) and not any(cd2.LAYOUTS[k][1] for k in cv2.TEXT_LAYOUTS))
    for k in cd2.LAYOUTS:  # 每种版式都能生成页面;需要实拍图的没给图时报错
        try:
            cd2.build_html(k, "cream", {"title": "标题", "sub": "副标题", "points": ["一", "二", "三"], "num": "3"}, "photo.png" if cd2.LAYOUTS[k][1] else "")
        except Exception as ex:
            rep.check(f"封面版式能生成:{k}", False, repr(ex))
    rep.check("封面:实拍版式不给图会报错", all(_raises2(lambda k=k: cd2.build_html(k, "cream", {"title": "t"}, "")) for k in cv2.PHOTO_LAYOUTS))
    rep.check("封面:文字会转义,不能注入", "&lt;script&gt;" in cd2.build_html("statement", "cream", {"title": "<script>x</script>"}))
    rg = _rd2.Random(7)
    cnt = {k: 0 for k in (1, 2, 3, 4)}
    for _ in range(4000):
        cnt[cv2.image_count({"image_count_weights": "20,30,30,20"}, rg)] += 1
    rep.check("图片张数:按 20/30/30/20 的比例随机,只有 1~4 张", all(abs(cnt[k] / 4000 - w) < 0.04 for k, w in zip((1, 2, 3, 4), (.2, .3, .3, .2))), str(cnt))
    rep.check("图片张数:可以设成固定张数,设置写错回到默认", cv2.image_count({"image_count_weights": "0,0,0,1"}) == 4 and cv2.image_count({"image_count_weights": "1,0,0,0"}) == 1
              and cv2.image_count({"image_count_weights": "abc"}) in (1, 2, 3, 4) and cv2.image_count({"image_count_weights": "0,0,0,0"}) in (1, 2, 3, 4))
    cc2 = cv2.clean_cover({"title": "这是一个特别特别特别长的封面标题啊啊啊", "sub": "", "points": ["配粥", "拌面", "炒菜", "烤肉", "第五条"], "num": "共3种", "unit": "种吃法啊啊啊啊啊", "scene": "桌上有「错字」的罐子"},
                          {"title": "腐乳吃法", "content": ""})
    rep.check("封面文字:限制长度,要点最多 4 条,数字取数字,画面描述里的文字去掉", len(cc2["title"]) <= 16 and len(cc2["points"]) == 4 and cc2["num"] == "3" and len(cc2["unit"]) <= 6 and "错字" not in cc2["scene"], str(cc2))
    cc3 = cv2.clean_cover({}, {"title": "腐乳的3种吃法", "content": "① 配粥:四分之一块\n② 拌面,加汤汁\n③ 炒空心菜\n结尾提问"})
    rep.check("封面文字:模型没给时,用标题和正文补(数字、要点)", cc3["title"] == "腐乳的3种吃法" and cc3["num"] == "3" and cc3["points"] == ["配粥", "拌面", "炒空心菜"], str(cc3))
    lay = lambda c, t="": cv2.choose(1, c, t)[0]
    rep.check("版式按内容挑:有要点用清单类,有数字用大数字,问句用问句封面", cv2.choose(0, {"points": ["a", "b", "c"]})[0] == "checklist"
              and "big_number" in [cv2.choose(i, {"num": "3", "points": []})[0] for i in range(5)] and "question" in [cv2.choose(i, {"title": "腐乳养胃吗"})[0] for i in range(5)])
    rep.check("版式轮换:相邻的稿子版式不同", len({cv2.choose(i, {"title": "普通标题", "points": []})[0] for i in range(5)}) == 5)
    rep.check("换封面:换成和现在不同的版式,没有实拍图时只用纯文字版式", all(cv2.next_style({"layout": "statement", "theme": "cream", "points": []}, False, _rd2.Random(i))[0] in ("question",) for i in range(20))
              and cv2.next_style({"layout": "statement", "theme": "cream", "points": ["a", "b", "c"], "num": "3"}, True, _rd2.Random(1))[0] != "statement")
    pl2, pc2 = cv2.plan(5, {"title": "腐乳怎么吃", "content": "", "cover": {"title": "腐乳怎么吃", "sub": "三种"}, "image_prompts": ["桌面上的腐乳罐,画面中不要出现任何文字", "手持腐乳,大字「不该有」"]}, "美食", "国圣", 4)
    rep.check("图片规划:共 n 条,第 1 条是封面说明(带封面文字),内页无字、不够的补齐", len(pl2) == 4 and pl2[0].startswith("封面·") and compliance.image_texts(pl2[:1]) and not compliance.image_texts(pl2[1:]) and all("不要出现任何文字" in p for p in pl2[1:]), str(pl2))
    pl1, _ = cv2.plan(5, {"title": "t", "content": "", "image_prompts": ["多余的"]}, "美食", "国圣", 1)
    rep.check("图片规划:只有封面时没有内页提示词", len(pl1) == 1)
    rep.check("封面里的高风险词会被处理,封面文字进合规扫描", w2._safe_text("全网最好的腐乳") == "全网很好的腐乳" or "最好" not in w2._safe_text("全网最好的腐乳"))
    rep.check("封面说明里的字会被合规扫描扫到", compliance.scan("", "", [], ["封面·大字海报:纯文字封面 文字:「全网第一的腐乳」"])["hits"] != [])
    if _sh2.which("chromium-headless-shell") or _sh2.which("chromium"):  # 真实渲染一张(只有装了浏览器的环境才跑)
        from pathlib import Path as _P2
        o2 = _P2(_tf2.mkdtemp()) / "c.png"
        cd2.render("statement", "ink", {"title": "腐乳别只配粥了", "sub": "3 种吃法", "tag": "吃法"}, o2)
        hd = o2.read_bytes()[:24]
        rep.check("封面渲染:真实生成 1080×1440 的 PNG", hd[:8] == b"\x89PNG\r\n\x1a\n" and int.from_bytes(hd[16:20], "big") == 1080 and int.from_bytes(hd[20:24], "big") == 1440)
    r = compliance.scan("", "抗炎早餐", [])
    rep.check("「抗炎」只提示(低风险),不算高风险", r["low"] == 1 and r["high"] == 0 and r["hits"][0]["category"] == "功效宣称", str(r["hits"]))
    seen = []
    orig_rev = llm2.revise
    llm2.revise = lambda out, hits: (seen.append([h["word"] for h in hits]) or {})
    try:
        w2._check_and_fix({"title": "t", "content": "全网第一的抗炎早餐", "alt_titles": [], "tags": [], "image_prompts": []}, True)
    finally:
        llm2.revise = orig_rev
    rep.check("自动修正:只改高风险词,不动「功效宣称」", bool(seen) and "第一" in seen[0] and "抗炎" not in seen[0], str(seen))
    rep.check("选题规则写进了提示词", all(x in t for x, t in [("不要把互不相关", llm2.ANGLES_SYSTEM), ("只写「本篇角度」里点明的那一个主题", llm2.WRITE_SYSTEM),
                                                   ("合理性", llm2.REVIEW_SYSTEM), ('"topics"', llm2.ANALYZE_SYSTEM), ("【配图数量】", llm2.WRITE_SYSTEM), ("封面文字(cover 里的所有字段)同样适用", llm2.WRITE_SYSTEM)]))
    rep.check("多个关键词在提示词里标明是并列的", "彼此独立" in llm2._project_text({"track": "美食", "keywords": ["腐乳", "咖啡"]}) and "彼此独立" not in llm2._project_text({"track": "美食", "keywords": ["腐乳"]}))
    rep.check("审稿维度含「合理性」", "合理性" in llm2.REVIEW_DIMS)
