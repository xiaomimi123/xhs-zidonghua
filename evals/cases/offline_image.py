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
    from pathlib import Path as _Path
    from app import db
    from app import covers as cv2, cards as cd2
    rep.check("封面:15 种版式(实拍加字 6 种、纯文字 8 种、纯产品图 1 种)、11 套配色", len(cd2.LAYOUTS) == 15 and len(cd2.THEMES) == 11 and set(cv2.TEXT_LAYOUTS + cv2.PHOTO_LAYOUTS + [cv2.PHOTO_ONLY]) == set(cd2.LAYOUTS)
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
    plain = {"title": "普通标题", "points": []}
    ten = [cv2.choose(1000 + i, plain, "", 7, i) for i in range(10)]
    rep.check("版式按批次轮换:10 篇里相邻不同、至少 7 种版式、图版式和字版式各一半", all(ten[i][0] != ten[i + 1][0] for i in range(9)) and len({x[0] for x in ten}) >= 7
              and sum(x[0] in cv2.PHOTO_LAYOUTS for x in ten) == 5, str([x[0] for x in ten]))
    rep.check("配色按批次轮换:10 篇里相邻不同、至少 8 种", all(ten[i][1] != ten[i + 1][1] for i in range(9)) and len({x[1] for x in ten}) >= 8 and len(cv2.THEME_KEYS) >= 10, str([x[1] for x in ten]))
    rep.check("不同任务的轮换顺序不一样(不是所有任务都从同一个版式开始)", len({cv2.choose(1, plain, "", pid, 0)[0] for pid in range(1, 12)}) >= 3)
    pts = {"title": "x", "points": ["a", "b", "c"], "num": "3"}
    ten2 = [cv2.choose(2000 + i, pts, "", 3, i)[0] for i in range(14)]
    rep.check("封面信息够时清单类和大数字也会轮到;不够时不会出(没要点不出清单类,没数字不出大数字)", {"checklist", "memo", "big_number"} & set(ten2) and
              all(x not in ("checklist", "memo", "swiss", "big_number") for x in [cv2.choose(i, plain, "", 3, i)[0] for i in range(14)]), str(ten2))
    rep.check("选出来的版式都满足封面信息的要求", all(cv2.choose(i, c, "", p, i)[0] in cv2.compatible(c, True) for c in (plain, pts, {"title": "腐乳怎么吃", "points": ["a", "b"]}) for p in (1, 2, 3) for i in range(12)))
    vs = [cv2.plan(5000 + i, {"title": "标题", "content": "", "cover": {"title": "标题"}, "image_prompts": []}, "美食", "品牌", 2, pid=4, idx=i)[1] for i in range(8)]
    rep.check("版式变体:同批里变体 0 / 1 都有,变体 1 的封面能渲染(标题在下 / 面板在上 / 拍立得反向)", {c["variant"] for c in vs} == {0, 1}
              and all("</div>" in cv2.cards.build_html(lay, "cream", {**vs[0], "variant": 1}, "x.png") for lay in ("photo_top", "sticker", "polaroid")))
    # 生图全局闸门:同时发出的请求数不超过设置(复刻配图和 Live 场景图共用)
    import threading as _thr, time as _tm2
    from app import imagegen as _ig2
    inflight, peak, lock2 = [0], [0], _thr.Lock()
    def fake_once(prompt, name, s=None, refs=None):
        with lock2:
            inflight[0] += 1; peak[0] = max(peak[0], inflight[0])
        _tm2.sleep(0.12)
        with lock2:
            inflight[0] -= 1
        return name
    o_once, o_get = _ig2._generate_once, _ig2.get_settings
    _ig2._generate_once, _ig2.get_settings = fake_once, lambda: {"image_api_concurrency": "3"}
    try:
        ths = [_thr.Thread(target=_ig2.generate_image, args=("p", f"n{i}")) for i in range(10)]
        t_a = _tm2.time(); [x.start() for x in ths]; [x.join() for x in ths]; el2 = _tm2.time() - t_a
    finally:
        _ig2._generate_once, _ig2.get_settings = o_once, o_get
    rep.check("生图全局闸门:10 个并发请求最多同时 3 个在途,且确实用满了 3 个", peak[0] == 3 and el2 < 0.12 * 10, f"峰值 {peak[0]} 耗时 {el2:.2f}")
    # 429 退避:按服务商 Retry-After 等,带抖动;等待期间不占名额
    class _R:
        status_code = 429; headers = {"retry-after": "7"}
        def json(self): return {"error": {"message": "slow down"}}
        text = ""
    e429 = _ig2._explain(_R())
    rep.check("429:读出服务商要求的等待秒数(Retry-After),最多 60 秒", isinstance(e429, _ig2.Transient) and e429.retry_after == 7)
    waits, tries = [], [0]
    def flaky(prompt, name, s=None, refs=None):
        tries[0] += 1
        if tries[0] < 3:
            ex = _ig2.Transient("限流"); ex.retry_after = 9; raise ex
        return "ok.png"
    o_once2, o_sleep, o_rw = _ig2._generate_once, _ig2.time.sleep, _ig2.RETRY_WAITS
    _ig2._generate_once, _ig2.time.sleep, _ig2.RETRY_WAITS = flaky, waits.append, (0, 4, 10, 25)
    try:
        got = _ig2.generate_image("p", "x")
    finally:
        _ig2._generate_once, _ig2.time.sleep, _ig2.RETRY_WAITS = o_once2, o_sleep, o_rw
    rep.check("限流自动退避重试:第 3 次成功;等待时间不短于服务商要求的 9 秒,闸门名额已还回", got == "ok.png" and tries[0] == 3 and len(waits) == 2 and all(w >= 9 for w in waits) and _ig2.GATE.n == 0, f"{waits} {_ig2.GATE.n}")
    rep.check("单张生图最多等 4 分钟,重试 4 次", _ig2.POLL_MAX == 240 and len(_ig2.RETRY_WAITS) == 4)
    from PIL import Image as _Img
    from app import imagegen as _ig
    _d = _Path(_tf2.mkdtemp())
    for nm, size in (("tall", (900, 1600)), ("wide", (1600, 900)), ("ok", (900, 1200))):
        _Img.new("RGB", size, (200, 120, 80)).save(_d / f"{nm}.png")
        _ig.fit_ratio(_d / f"{nm}.png", _d / f"{nm}_o.png", 0.75)
    sz = {nm: _Img.open(_d / f"{nm}_o.png").size for nm in ("tall", "wide", "ok")}
    rep.check("产品原图裁成 3:4:竖拍 9:16 裁上下、横图裁左右、已经是 3:4 的原样", sz["tall"] == (900, 1200) and abs(sz["wide"][0] / sz["wide"][1] - 0.75) < 0.01 and sz["ok"] == (900, 1200), str(sz))
    rep.check("生图比例解析:3:4 / 9:16 / 像素 / 认不出来按 3:4", _ig.target_ratio("3:4") == 0.75 and abs(_ig.target_ratio("9:16") - 0.5625) < 1e-6 and abs(_ig.target_ratio("1024x1536") - 2 / 3) < 1e-6 and _ig.target_ratio("auto") == 0.75)
    rep.check("生图比例默认是小红书的 3:4", db.DEFAULT_SETTINGS["image_size"] == "3:4")
    # 场景和产品匹配:咖啡不放在灶台炒锅边,零食不放厨房烹饪场景,两用的不放灶台;产品资料里写的场景优先
    coffee = {"name": "冻干咖啡", "category": "冻干咖啡", "profile": {"scenes": ["居家下午茶", "自制咖啡角", "早餐搭配", "办公提神"]}}
    cinn = {"name": "肉桂粉", "category": "肉桂粉", "profile": {"scenes": ["制作锡兰肉桂拿铁", "烘焙甜点调味", "冲泡热饮", "烹饪肉类菜肴"]}}
    brn = {"name": "布朗尼可可坚果脆", "category": "布朗尼饼干", "profile": {"scenes": ["办公桌面下午茶", "追剧休闲零食"]}}
    fur = {"name": "豆腐乳", "category": "腐乳", "profile": {"scenes": ["早餐佐餐", "米饭拌饭", "配粥下饭"]}}
    oil = {"name": "花生油", "category": "食用油", "profile": {"scenes": ["厨房烹饪"]}}
    allsc = lambda pr: {cv2.scene("美食", d, i, pr) for d in range(30) for i in range(1, 5)}
    bad = ("灶台", "锅", "油渍")
    rep.check("产品类型:冲泡饮品 / 零食 / 两用 / 烹饪调味 / 一般食品", [cv2.product_kind(x) for x in (coffee, brn, cinn, oil, fur, None)] == ["drink", "snack", "mixed", "cook", "food", "food"],
              str([cv2.product_kind(x) for x in (coffee, brn, cinn, oil, fur, None)]))
    rep.check("场景匹配:咖啡 / 零食 / 肉桂粉(两用) / 腐乳 / 不知道产品的,都不会出现灶台和锅", all(not any(b in sc for b in bad) for x in (coffee, brn, cinn, fur, None) for sc in allsc(x)))
    rep.check("场景匹配:只有烹饪调味类产品才会出现灶台", any("灶台" in sc for sc in allsc(oil)) and not any("灶台" in sc for sc in allsc(coffee)))
    rep.check("产品资料里写的场景优先:办公提神 → 办公场景、早餐搭配 → 早餐桌,同一篇每张换场景", any("办公" in sc for sc in allsc(coffee)) and any("早餐" in sc for sc in allsc(coffee))
              and len({cv2.scene("美食", 9, k, coffee) for k in range(1, 4)}) == 3)
    rep.check("非美食赛道不受影响;没传产品时和以前一样可用", cv2.scene("穿搭", 3, 1, coffee) in cv2.GENERIC_SCENES and cv2.scene("美食", 3, 1) in cv2.FOOD_SCENES)
    rep.check("补齐的内页提示词也按产品挑场景(不会是灶台)", all("灶台" not in p for d in range(20) for p in cv2.inner_prompts([], 3, "美食", d, "咖啡", coffee)))
    from app import products as _pd
    rep.check("写作时告诉模型:饮品不放灶台炒锅,只有烹饪调味类才出现厨房灶台", "不要放在灶台" in _pd.block({"name": "咖啡", "profile": {}}) and "烹饪调味类产品才出现厨房灶台" in _pd.block({"name": "咖啡", "profile": {}}))
    # 纯产品图封面(第 2 类封面):任务有产品时和另外两类轮着来
    twelve = [cv2.choose(3000 + i, plain, "", 5, i, True)[0] for i in range(12)]
    kinds12 = [cv2.kind_of(x) for x in twelve]
    rep.check("有产品:三类封面(实拍加字 / 纯文字 / 纯产品图)轮着来,相邻类型不同,每类约 1/3", set(kinds12) == {"photo_text", "html", "photo"} and all(kinds12[i] != kinds12[i + 1] for i in range(11))
              and kinds12.count("photo") == 4, str(kinds12))
    rep.check("没有产品:不出纯产品图,和以前一样图 / 字交替", "photo_only" not in [cv2.choose(3000 + i, plain, "", 5, i)[0] for i in range(12)] and "photo_only" not in cv2.compatible(plain, True)
              and "photo_only" in cv2.compatible(plain, True, True) and "photo_only" not in cv2.compatible(plain, False, True))
    rep.check("封面类型:纯产品图 / 实拍加字 / 纯文字", cv2.kind_of("photo_only") == "photo" and cv2.kind_of("split_top") == "photo_text" and cv2.kind_of("quote") == "html" and cv2.kind_of("question") == "html")
    ppl, pcc = cv2.plan(6000, {"title": "白粥配腐乳", "content": "", "cover": {"title": "白粥配腐乳", "sub": "早餐"}, "image_prompts": []}, "美食", "国圣", 2, pid=5, idx=2, has_product=True)
    cc_photo = {**pcc, "layout": "photo_only", "kind": "photo"}
    rep.check("纯产品图封面:记下类型,封面说明里没有字(过图上文字扫描也没有),渲染出来只有一张图", pcc["kind"] == cv2.kind_of(pcc["layout"]) and pcc["product"] is True
              and "文字" not in cv2.cover_line(cc_photo) and not compliance.image_texts([cv2.cover_line(cc_photo)]) and "<h1" not in cv2.cards.build_html("photo_only", "cream", cc_photo, "x.png") and "x.png" in cv2.cards.build_html("photo_only", "cream", cc_photo, "x.png"))
    rep.check("换封面:有产品又有现成实拍图时能换到纯产品图;没有产品时不会", "photo_only" in {cv2.next_style({"layout": "statement", "theme": "cream", "points": [], "product": True}, True, _rd2.Random(i))[0] for i in range(60)}
              and "photo_only" not in {cv2.next_style({"layout": "statement", "theme": "cream", "points": []}, True, _rd2.Random(i))[0] for i in range(60)})
    rep.check("换封面:换成和现在不同的版式,没有实拍图时只用纯文字版式", all(cv2.next_style({"layout": "statement", "theme": "cream", "points": []}, False, _rd2.Random(i))[0] in ("quote", "stripe") for i in range(20))
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
