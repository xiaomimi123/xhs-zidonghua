"""不依赖数据库的检查:数字解析、合规扫描、统计评分、提示词模板、图片转存、数据清洗。"""


def run(rep):
    from app import compliance, llm, review, stats
    from app.notes import num
    print("\n[数字解析]")
    for raw, want in [("1.2万", 12000), ("3.4w", 34000), ("5k", 5000), (800, 800), ("", 0), ("赞 2.5万+", 25000)]:
        rep.check(f"num({raw!r})", num(raw) == want, f"得到 {num(raw)}")

    print("\n[合规扫描]")
    words = lambda r: {h["word"] for h in r["hits"]}
    cases = [
        ("排除短语不误报", "油皮底妆3步", "第一步先保湿,第一次用就喜欢", [], ["第一"]),
        ("排名类第一要报", "全网第一的粉底", "", ["第一"], []),
        ("序数第一不误报", "", "你们第一笔预算花在哪?第一支口红买的什么", [], ["第一"]),
        ("极限词+导流", "", "最好用的,想要的加我微信", ["最好", "微信"], []),
        ("长词优先", "", "最高级的质感", ["最高级"], ["最高"]),
        ("AI 腔", "", "值得注意的是,综上所述", ["值得注意的是", "综上所述"], []),
        ("正常内容零命中", "油皮早八5分钟底妆", "1️⃣ 妆前乳只涂T区\n2️⃣ 粉底少量多次\n你们用什么定妆?", [], None),
    ]
    for name, title, content, must, must_not in cases:
        r = compliance.scan(title, content, [])
        if must_not is None:
            rep.check(f"扫描:{name}", not r["hits"], f"误报 {words(r)}")
        else:
            rep.check(f"扫描:{name}", set(must) <= words(r) and not (set(must_not) & words(r)), f"命中 {words(r)}")
    r = compliance.scan("一" * 21, "", [])
    rep.check("扫描:标题超 20 字", any(h["category"] == "标题超长" for h in r["hits"]))
    r = compliance.scan("", "到手价【补充:价格】,用了【补充：时长】", [])
    rep.check("占位符识别", len(r["placeholders"]) == 2, str(r["placeholders"]))
    for text, n in [("我这次做了个左右脸实测。", 1), ("坚持了3个月。", 1), ("我拿3款粉底对比。", 1),
                    ("建议你这样测:左右脸各用一款。", 0), ("我用了【补充:真实时长】。", 0), ("第一步先保湿。", 0)]:
        r = compliance.scan("", text, [])
        rep.check(f"经历描述自动确认(不拦截):{text[:12]}", len(r["confirmed"]) == n and not r["verify"], f"期望 {n} 得到 {len(r['confirmed'])}")

    print("\n[统计与评分]")
    f = stats.features({"title": "油皮3步!", "content": "痛点\n\n1. 第一步\n2. 第二步\n你们呢?", "tags": ["a", "b"], "images": []})
    rep.check("特征:标题含数字/标点", f["t_digit"] and f["t_mark"])
    rep.check("特征:分点与互动", f["list_ratio"] > 0.3 and f["cta"], str(f))
    rep.check("等级边界", [review.grade_of(x) for x in (95, 90, 85, 72, 60, 59)] == ["S", "S", "A", "B", "C", "D"])
    q = review.quant({"title": "一" * 15, "content": "字" * 100, "tags": ["a"] * 6},
                     {"title_len": 15, "body_len": 100, "paras": 1, "emoji_100": 0, "list_ratio": 0, "cta": 0, "tags": 6})
    rep.check("量化贴合:完全贴合 100 分", q["score"] == 100, str(q))

    print("\n[提示词模板]")
    for name, fn in [("写作", lambda: llm.WRITE_SYSTEM.format(banned="x")),
                     ("审稿", lambda: llm.REVIEW_SYSTEM.format(banned="x", comments_rule="", comments_field="")),
                     ("审稿+评论", lambda: llm.REVIEW_SYSTEM.format(banned="x", comments_rule=llm.COMMENTS_RULE, comments_field=llm.COMMENTS_FIELD))]:
        try:
            out = fn()
            rep.check(f"模板可格式化:{name}", "{banned}" not in out and "【补充" in out)
        except Exception as e:
            rep.check(f"模板可格式化:{name}", False, repr(e))

    print("\n[图片转存]")
    from app import mirror
    from app.notes import localize
    heads = {"jpg": b"\xff\xd8\xff\xe0" + b"0" * 20, "png": b"\x89PNG\r\n\x1a\n" + b"0" * 20, "gif": b"GIF89a" + b"0" * 20,
             "webp": b"RIFF\x00\x00\x00\x00WEBPVP8 ", "avif": b"\x00\x00\x00\x1cftypavif" + b"0" * 12}
    rep.check("识别图片格式", all(mirror.sniff(v) == k for k, v in heads.items()))
    rep.check("拒绝 SVG / HTML / 空内容", all(mirror.sniff(x) is None for x in (b"<svg xmlns='x'></svg>", b"<html>403</html>", b"")))
    for url in ("http://127.0.0.1/a.png", "http://10.0.0.5/a.png", "http://192.168.1.100:18765/a.png", "http://169.254.169.254/x",
                "http://[::1]/a.png", "http://100.64.0.1/a.png", "ftp://example.com/a.png", "file:///etc/passwd"):
        try:
            mirror.check_url(url)
            rep.check(f"拦截内网地址:{url}", False, "没有被拦截")
        except ValueError:
            rep.check(f"拦截内网地址:{url}", True)
    try:
        mirror.check_url("http://198.18.0.5/a.png")
        rep.check("放行 fake-ip 代理网段", True)
    except ValueError as e:
        rep.check("放行 fake-ip 代理网段", False, str(e))
    n1 = mirror.name_for("https://cdn.example.com/a/b.jpg?sign=111&t=1")
    n2 = mirror.name_for("https://cdn.example.com/a/b.jpg?sign=222&t=9")
    rep.check("同一张图换签名只存一份", n1 == n2 and n1 != mirror.name_for("https://cdn.example.com/a/c.jpg"))
    n = localize({"images": ["https://x/1.jpg", "https://x/2.jpg"], "images_local": ["a.jpg", ""], "cover": "https://x/1.jpg", "cover_local": "a.jpg"})
    rep.check("对外地址:已转存用本地,失败的保留原链接", n["images"] == ["/note_images/a.jpg", "https://x/2.jpg"] and n["cover"] == "/note_images/a.jpg", str(n))

    print("\n[数据清洗]")
    import datetime
    from app.notes import author_id_of, clean_text, norm_date
    ref = datetime.date(2026, 11, 2)
    for raw, want in [("编辑于 2025-11-05", "2025-11-05"), ("2025-06-16", "2025-06-16"), ("09-02", "2026-09-02"), ("编辑于 08-14", "2026-08-14"),
                      ("12-25", "2025-12-25"), ("3天前", "2026-10-30"), ("昨天 12:30", "2026-11-01"), ("刚刚", "2026-11-02"),
                      ("2025年6月8日", "2025-06-08"), ("02-30", "02-30"), ("神秘文本", "神秘文本"), ("", "")]:
        got = norm_date(raw, ref)
        rep.check(f"发布时间:{raw or '(空)'} → {want or '(空)'}", got == want, f"得到 {got}")
    rep.check("去零宽空格", clean_text("a​b﻿c⁠d") == "abcd")
    rep.check("保留 emoji 组合符(🙋‍♀️)", clean_text("好\U0001F64B‍♀️") == "好\U0001F64B‍♀️")
    rep.check("不间断空格换成普通空格", clean_text("a b") == "a b")
    rep.check("博主 ID 从主页链接提取", author_id_of({"author_url": "https://www.xiaohongshu.com/user/profile/6a8eaa12000000001e00cccc"}) == "6a8eaa12000000001e00cccc")
    rep.check("传了 author_id 就用传的", author_id_of({"author_id": "X1", "author_url": "https://x/user/profile/Y2"}) == "X1")
