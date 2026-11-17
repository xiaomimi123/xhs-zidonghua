"""封面指纹对号:发布时改了标题 / 正文的笔记,按封面对回稿子。指纹稳定性、区分度、对号条件(明显优于第二名 / 账号 / 日期 / 一对一)、
下载失败的处理、对号接入、手动绑定窗口里封面一致排最前面。用合成的图(不依赖真实封面);真实封面的校准数据见 app/coverhash.py 的说明。"""
import io
import json
import random
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance


def _card(seed: int, layout: int = 0) -> Image.Image:
    """合成一张「文字卡片」封面:同一个底色和版式,只有文字块的位置 / 长短不同(最难分的那一类)。"""
    r = random.Random(seed)
    im = Image.new("RGB", (1086, 1448), (250, 235, 220) if layout == 0 else (30, 40, 60))
    d = ImageDraw.Draw(im)
    for k in range(r.randint(3, 5)):
        y = 200 + k * 230 + r.randint(-20, 20)
        w = r.randint(300, 880)
        d.rectangle((90, y, 90 + w, y + r.randint(70, 120)), fill=(40, 40, 40) if layout == 0 else (240, 240, 240))
        d.text((100, y + 140), "标题" * r.randint(2, 9), fill=(90, 60, 50))
    return im


def _publish(im: Image.Image, q: int = 85, shift: float = 1.0) -> Image.Image:
    """模拟发出去又取回来:缩到 1080 宽、JPEG 压缩,可以再整体调亮度对比度。"""
    w, h = im.size
    im = im.resize((1080, int(1080 * h / w)), Image.LANCZOS)
    if shift != 1.0:
        im = ImageEnhance.Brightness(im).enhance(shift)
        im = ImageEnhance.Contrast(im).enhance(1 + (shift - 1) / 2)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=q)
    return Image.open(io.BytesIO(buf.getvalue()))


def run(rep, env):
    from app import coverfp, coverhash as ch, db, imagegen, mirror, qianfan

    print("\n[封面指纹]")
    cards = [_card(i) for i in range(8)]
    fps = [ch.fingerprint(c) for c in cards]
    same_e = [ch.vec_dist(f[1], ch.fingerprint(_publish(c))[1]) for c, f in zip(cards, fps)]
    same_m = [ch.vec_dist(f[1], ch.fingerprint(_publish(c, 72, 1.04))[1]) for c, f in zip(cards, fps)]
    dh_same = [ch.dh_dist(f[0], ch.fingerprint(_publish(c))[0]) for c, f in zip(cards, fps)]
    diff = [ch.vec_dist(fps[i][1], fps[j][1]) for i in range(8) for j in range(i + 1, 8)]
    rep.check("指纹稳定:同一张图缩放 + JPEG 压缩后距离极小(≤ 0.03),整体亮度对比度各变 4% + JPEG 70 也很小(≤ 0.08);粗筛的 dHash 距离 ≤ 16",
              max(same_e) <= 0.03 and max(same_m) <= 0.08 and max(dh_same) <= 16, f"{max(same_e):.3f} {max(same_m):.3f} {max(dh_same)}")
    rep.check("指纹区分:同底色同版式、只有文字块不同的卡片,互相的距离都明显大于同图的波动(最小距离 > 2 倍的对号阈值)", min(diff) > 2 * ch.SAME, f"最小 {min(diff):.3f}")
    other = ch.fingerprint(_card(1, layout=1))
    rep.check("不同底色的封面距离更大", ch.vec_dist(fps[1][1], other[1]) > 0.2)
    rep.check("指纹可以存成十六进制再读回(dHash)", ch.from_hex(ch.to_hex(fps[0][0])) == fps[0][0] and ch.from_hex("zz") is None and ch.from_hex("") is None)
    rep.check("读不出来的图返回空,不报错", ch.fp_bytes(b"not an image") is None and ch.fp_file(Path("/不存在.png")) is None)

    # ---- 对号 ----
    o_dir, o_kick = imagegen.IMG_DIR, coverfp.kick
    tmp = Path(tempfile.mkdtemp())
    imagegen.IMG_DIR = tmp
    coverfp.kick = lambda *a, **k: False   # 测试里不要起后台线程(它会真去下载)
    pj = db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('美食','[]','')").lastrowid
    names = []
    for i in range(6):
        cards[i % 8].save(tmp / f"cv_{i}.png")
        names.append(f"cv_{i}.png")
    mkd = lambda title, files, acct="": db.q_exec("INSERT INTO drafts(project_id,title,content,status,images,account_id) VALUES(?,?,?,'ready',?,?)",
                                                   pj, title, "正文", json.dumps(files), acct).lastrowid
    try:
        d0, d1, d2 = mkd("封面测试稿零", [names[0]]), mkd("封面测试稿一", [names[1]], "accA"), mkd("封面测试稿二", [names[2]])
        n_done = coverfp.warm_drafts()
        rep.check("稿子封面指纹:后台计算只算第一张图,算过的不再重算", n_done >= 3 and coverfp.warm_drafts() == 0 and set(coverfp.get("draft", names[:3])) == set(names[:3]), str(n_done))
        today = __import__("time").strftime("%Y-%m-%d")
        note = lambda nid, src, acct="", day=today: {"note_id": nid, "title": f"发布时改过的标题{nid}", "cover": f"http://ci.example/{nid}", "account_id": acct, "published_at": day, "views": 5}
        n0, n1, nz = note("N0", 0), note("N1", 1, "accA"), note("NZ", 7)
        coverfp.put("note", "N0", ch.fingerprint(_publish(cards[0])), n0["cover"])
        coverfp.put("note", "N1", ch.fingerprint(_publish(cards[1], 72, 1.03)), n1["cover"])
        coverfp.put("note", "NZ", ch.fingerprint(_publish(cards[7])), nz["cover"])   # 不是我们任何一篇稿子的封面
        drafts = [{"id": d, **db.q_one("SELECT images, account_id, created FROM drafts WHERE id=?", d)} for d in (d0, d1, d2)]
        got = coverfp.match([n0, n1, nz], drafts)
        rep.check("按封面对号:封面一致的笔记对上各自的稿子(标题全改了也行);别人的封面(不是我们任何稿子)不乱对;带 matched_by 和距离",
                  got.get(d0, {}).get("note_id") == "N0" and got.get(d1, {}).get("note_id") == "N1" and d2 not in got and len(got) == 2
                  and got[d0]["matched_by"] == "cover" and got[d0]["cover_d"] <= ch.SAME, str({k: v.get("note_id") for k, v in got.items()}))
        # 账号冲突 / 发布时间早于稿子生成
        g2 = coverfp.match([note("N1", 1, "accB")], [drafts[1]])
        g3 = coverfp.match([note("N0", 0, day="2020-01-01")], [drafts[0]])
        rep.check("账号不一致不对(稿子指定了 accA,笔记是 accB);笔记发布日期比稿子生成早很多也不对", g2 == {} and g3 == {}, str((g2, g3)))
        # 分不清:两篇稿子用了同一张封面(重复 / 克隆),不自动对,留给人挑
        dup = mkd("同封面的另一篇", [names[0]])
        drafts2 = drafts + [{"id": dup, **db.q_one("SELECT images, account_id, created FROM drafts WHERE id=?", dup)}]
        rep.check("两篇稿子封面一样(分不清是哪篇):不自动对,留给人在候选里挑", coverfp.match([n0], drafts2) == {})
        # 一对一:两条笔记封面都像同一篇稿子,只给更像的那条
        near = note("N0b", 0)
        coverfp.put("note", "N0b", ch.fingerprint(_publish(cards[0], 60, 1.06)), near["cover"])
        g4 = coverfp.match([near, n0], [drafts[0]])
        rep.check("一篇稿子只对一条笔记:两条笔记都像它时,只对更像的那条", len(g4) == 1 and g4[d0]["note_id"] == "N0", str({k: v.get("note_id") for k, v in g4.items()}))

        # ---- 下载封面:成功 / 失败 / 不是图片 ----
        calls = []
        o_fetch = mirror.fetch
        buf = io.BytesIO(); _publish(cards[3]).save(buf, "JPEG")
        def fake_fetch(client, url):
            calls.append(url)
            if url.endswith("bad"):
                raise IOError("HTTP 403,链接可能已过期")
            if url.endswith("txt"):
                return b"hello"
            return buf.getvalue()
        mirror.fetch = fake_fetch
        try:
            items = [{"note_id": "D1", "cover": "http://ci.example/ok", "title": "x"}, {"note_id": "D2", "cover": "http://ci.example/bad", "title": "y"},
                     {"note_id": "D3", "cover": "http://ci.example/txt", "title": "z"}, {"note_id": "D4", "title": "没有封面地址"}]
            n_ok = coverfp.warm_notes(items)
            first_calls = len(calls)
            coverfp.warm_notes(items)
        finally:
            mirror.fetch = o_fetch
        st = coverfp._known("note")
        rep.check("下载笔记封面:成功的存指纹;下载失败 / 不是图片的记下原因;没有封面地址的跳过;已经处理过的短时间内不重复下载",
                  n_ok == 1 and st["D1"][0] and not st["D2"][0] and "403" in st["D2"][2] and not st["D3"][0] and "D4" not in st and first_calls == 3 and len(calls) == 3, str((n_ok, calls)))

        # ---- 接入对号:标题对不上、封面一致 → 对上;标题对得上的照旧 ----
        ttl = mkd("发布前的原标题甲乙丙丁", [names[4]])
        ttl2 = mkd("标题一模一样的稿子", [names[5]])
        coverfp.warm_drafts()
        coverfp.put("note", "NC", ch.fingerprint(_publish(cards[4])), "http://ci.example/NC")
        itms = [{"note_id": "NC", "title": "完全改掉了的新标题戊己庚辛", "cover": "http://ci.example/NC", "account_id": "", "published_at": today, "views": 9, "likes": 1, "collects": 0, "comments": 0},
                {"note_id": "NT", "title": "标题一模一样的稿子", "cover": "http://ci.example/NT", "account_id": "", "published_at": today, "views": 3, "likes": 0, "collects": 0, "comments": 0}]
        perf = qianfan.match_drafts(itms)
        rep.check("对号接入:标题全改了、封面一致的笔记按封面对上(matched_by=cover);标题一样的照旧按标题对上",
                  perf.get(ttl, {}).get("note_id") == "NC" and perf[ttl].get("matched_by") == "cover" and perf.get(ttl2, {}).get("note_id") == "NT" and "matched_by" not in perf[ttl2], str({k: v.get("note_id") for k, v in perf.items() if k in (ttl, ttl2)}))
        # 手动对上的优先,封面不会把它抢走
        db.q_exec("UPDATE drafts SET qf_note_id='NT' WHERE id=?", ttl)
        perf2 = qianfan.match_drafts(itms)
        rep.check("手动对上的优先:稿子已经手动绑了笔记 NT,就不会再按封面被别的笔记抢走", perf2.get(ttl, {}).get("note_id") == "NT" and perf2.get(ttl2, {}).get("note_id") != "NT", str({k: v.get("note_id") for k, v in perf2.items() if k in (ttl, ttl2)}))
        db.q_exec("UPDATE drafts SET qf_note_id='' WHERE id=?", ttl)

        # ---- 手动绑定窗口:封面一致的排最前面 ----
        cd = coverfp.note_cover_dists([names[4]], itms)
        cl = qianfan.closest_notes("发布前的原标题甲乙丙丁", "", itms, set(), today, 3, cd)
        rep.check("手动绑定的候选:封面一致的笔记排最前面并标 cover_same(哪怕标题完全不像);距离存在", cl and cl[0]["note_id"] == "NC" and cl[0]["cover_same"] and cl[0]["cover_d"] <= 0.04, str(cl[:1]))
        cl2 = qianfan.closest_notes("发布前的原标题甲乙丙丁", "", itms, set(), today, 3, {})
        rep.check("没有封面指纹时候选照旧只看标题 / 账号 / 日期(封面标记为 False)", all(not c["cover_same"] for c in cl2))

        # ---- 同步入库:封面地址会存下来 ----
        qianfan.ingest({"kind": "notes", "shop_id": "cvshop", "shop_name": "封面店", "items": [{"note_id": "ING1", "title": "入库的笔记", "views": 1, "cover": "http://ci.example/ING1?imageView2/2/w/1080/format/jpg"}]})
        saved = [x for x in qianfan.notes_all("cvshop")[0] if x["note_id"] == "ING1"]
        rep.check("采集端传来的封面地址会存进笔记数据", saved and saved[0].get("cover", "").startswith("http://ci.example/ING1"), str(saved))
    finally:
        imagegen.IMG_DIR, coverfp.kick = o_dir, o_kick
        db.q_exec("DELETE FROM drafts WHERE project_id=?", pj)
        db.q_exec("DELETE FROM cover_fp")
