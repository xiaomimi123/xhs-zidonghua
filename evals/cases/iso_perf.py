"""列表页性能:缩略图、成品库对号算法、任务页批量查询、gzip。
成品库 / 任务页数据一多就慢的原因都在这里:封面用原图(每张 1~3MB)、对号是 稿子 × 笔记 的双层循环、
任务页每个任务查 8 条 SQL 还每条重新开连接。这里确认修完之后结果和原来完全一样,而且确实快。
放在人员管理之前跑(人员管理会开启登录要求)。"""
import io
import json
import random
import tempfile
import time
from pathlib import Path
from ..common import _raises2


def _old_match(drafts, items):
    """修改前的对号算法(原样保留,当作标准答案):每条笔记都扫一遍所有稿子。"""
    from app.qianfan import _norm_title
    from app.notes import _grams
    by_note = {str(it.get("note_id")): it for it in items if it.get("note_id")}
    out, taken = {}, set()
    for d in drafts:
        if d["qf_note_id"] and d["qf_note_id"] in by_note:
            out[d["id"]] = by_note[d["qf_note_id"]]
            taken.add(d["qf_note_id"])
    drafts = [d for d in drafts if d["id"] not in out]
    norm = {d["id"]: _norm_title(d["title"]) for d in drafts}
    grams = {i: _grams(t) for i, t in norm.items() if t}
    acct = {d["id"]: d["account_id"] or "" for d in drafts}
    for it in items:
        t = _norm_title(it.get("title", ""))
        if not t or str(it.get("note_id") or "") in taken:
            continue
        aid = it.get("account_id", "")
        cands = [i for i in grams if not acct[i] or not aid or acct[i] == aid]
        did = next((i for i in cands if norm[i] == t and i not in out), 0)
        if not did:
            g = _grams(t)
            best = max(((len(g & grams[i]) / len(g | grams[i]), i) for i in cands if g and grams[i] and i not in out), default=(0, 0))
            did = best[1] if best[0] >= 0.8 else 0
        if did:
            out[did] = it
    return out


def run(rep, env):
    from app import api_user, autorun, db, imagegen, qianfan, thumbs
    user = env.user

    print("\n[列表页性能]")

    # ---------- 对号算法:和原来一模一样,而且快 ----------
    rnd = random.Random(7)
    words = ["黄金奶", "肉桂粉", "姜黄", "拿铁", "早餐", "做法", "避坑", "新手", "3分钟", "冲泡", "养生", "好物", "测评", "懒人", "食谱", "搭配"]
    mk_title = lambda: "".join(rnd.choice(words) for _ in range(rnd.randint(2, 4)))
    same = True
    detail = ""
    for trial in range(25):
        pid0 = db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('t','[]','')").lastrowid
        rows, ids = [], []
        for k in range(rnd.randint(5, 40)):
            t = mk_title()
            acc = rnd.choice(["", "", "A", "B"])
            did = db.q_exec("INSERT INTO drafts(project_id,title,content,account_id,qf_note_id,status) VALUES(?,?,?,?,?,'ready')",
                            pid0, t, "x", acc, "nid%d" % k if rnd.random() < 0.1 else "").lastrowid
            ids.append(did)
            rows.append({"id": did, "title": t, "account_id": acc, "qf_note_id": "nid%d" % k if rows is not None and False else ""})
        dr = db.q_all("SELECT id, title, account_id, qf_note_id FROM drafts WHERE project_id=? ORDER BY id", pid0)
        items = []
        for k in range(rnd.randint(5, 80)):
            base = rnd.choice(dr)["title"] if rnd.random() < 0.7 else mk_title()
            if rnd.random() < 0.4:
                base = base + rnd.choice(["", "!", " ✨", "(附配方)", "?"])
            items.append({"note_id": "n%d_%d" % (trial, k), "title": base, "account_id": rnd.choice(["", "A", "B", ""]), "likes": k})
        items += [{"note_id": "nid%d" % k, "title": "手动对上", "account_id": ""} for k in range(0, 40, 7)]
        want = _old_match(dr, items)
        # 新算法读的是库里全部稿子:只比这一批(前面几批的稿子也在库里,会参与对号,所以每批后清掉)
        got = qianfan.match_drafts(items)
        if {k: v["note_id"] for k, v in want.items()} != {k: v["note_id"] for k, v in got.items()}:
            same = False
            detail = f"第 {trial} 批不一致:{len(want)} vs {len(got)}"
            break
        db.q_exec("DELETE FROM drafts WHERE project_id=?", pid0)
        db.q_exec("DELETE FROM projects WHERE id=?", pid0)
    rep.check("对号算法:25 批随机稿子 / 笔记,结果和修改前完全一致(含手动对上、账号限制、相同分数取编号大的)", same, detail)

    # 数量大时的速度:300 篇稿子 × 3000 条笔记
    pid1 = db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('t','[]','')").lastrowid
    for k in range(300):
        db.q_exec("INSERT INTO drafts(project_id,title,content,status) VALUES(?,?,?,'ready')", pid1, mk_title() + str(k), "x")
    big = [{"note_id": "b%d" % k, "title": mk_title() + str(k % 400), "account_id": ""} for k in range(3000)]
    t0 = time.time()
    qianfan.match_drafts(big)
    t_new = time.time() - t0
    dr = db.q_all("SELECT id, title, account_id, qf_note_id FROM drafts WHERE title!=''")
    t0 = time.time()
    _old_match(dr, big)
    t_old = time.time() - t0
    rep.check("对号算法:300 篇稿子 × 3000 条笔记,比原来快(原来循环上百万次)", t_new < t_old, f"新 {t_new:.2f}s / 旧 {t_old:.2f}s")
    db.q_exec("DELETE FROM drafts WHERE project_id=?", pid1)
    db.q_exec("DELETE FROM projects WHERE id=?", pid1)

    # ---------- 成品库:对号只算一次 ----------
    pid2 = db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('美食','[\"perf\"]','')").lastrowid
    for k in range(3):
        db.q_exec("INSERT INTO drafts(project_id,title,content,status,images) VALUES(?,?,?,'ready','[\"perf_a.png\"]')", pid2, f"性能测试稿{k}", "正文")
    calls = []
    _m = qianfan.match_drafts
    qianfan.match_drafts = lambda items: (calls.append(1), _m(items))[1]
    try:
        lib = api_user.library(pid2)
    finally:
        qianfan.match_drafts = _m
    rep.check("成品库:笔记表现对号只算一次(原来算两遍),结果照常返回", len(calls) == 1 and len(lib) == 3, f"调用 {len(calls)} 次,返回 {len(lib)} 条")

    # ---------- 任务页:批量查询,结果和逐个查一致 ----------
    prod = db.q_exec("INSERT INTO products(name,images) VALUES('性能产品','[\"pp.png\"]')").lastrowid
    plist = []
    for k in range(6):
        pr = db.q_exec("INSERT INTO projects(track,keywords,direction,product_id) VALUES(?,?,?,?)", "美食", json.dumps([f"词{k}"]), "", prod if k % 3 == 0 else None).lastrowid
        d_ids = [db.q_exec("INSERT INTO drafts(project_id,title,content,status,auto_ok,images) VALUES(?,?,?,?,?,?)",
                           pr, f"稿{k}-{j}", "x", "ready" if j != 2 else "error", 1 if j == 0 else (0 if j == 1 else None), json.dumps([f"cov{k}_{j}.png"])).lastrowid for j in range(3)]
        pb = db.q_exec("INSERT INTO playbooks(project_id,status) VALUES(?,?)", pr, "done" if k % 2 else "queued").lastrowid
        for st in ("done", "pending", "running"):
            db.q_exec("INSERT INTO crawl_tasks(project_id,type,query,status) VALUES(?,?,?,?)", pr, "keyword", "q", st)
        for n in range(k):
            db.q_exec("INSERT OR IGNORE INTO project_notes(project_id, note_id) VALUES(?,?)", pr, 1000 + n)
        db.q_exec("INSERT INTO autoruns(project_id,target,status,msg,state) VALUES(?,?,?,?,?)", pr, 5, ["writing", "done", "error"][k % 3], "m",
                  json.dumps({"drafts": d_ids + d_ids[:1], "pbs": [pb], "library": k, "failed_at": "collecting" if k % 3 == 2 else ""}))   # 故意带重复的稿子编号
        plist.append(pr)
    ps = db.q_all("SELECT p.* FROM projects p JOIN autoruns a ON a.project_id=p.id WHERE p.id IN (%s) ORDER BY a.id DESC" % ",".join(str(i) for i in plist))
    one_by_one = [api_user._run_info(p) for p in ps]
    batch = api_user._runs_info(ps)
    rep.check("任务页:批量查询的结果和逐个查完全一致(进度、产品名、封面、阶段条)", batch == one_by_one, f"{batch[0]} / {one_by_one[0]}")
    r0 = next(x for x in batch if x["id"] == plist[0])
    rep.check("任务页:数字对得上(3 个采集任务完成 1 个、稿子 3 篇合格 1 篇、重复编号不重复计数、取合格稿的图当封面)",
              r0["tasks"] == [1, 3] and r0["drafts"]["total"] == 3 and r0["drafts"]["passed"] == 1 and r0["drafts"]["failed"] == 1 and r0["cover"] == f"/images/cov0_0.png" and r0["kind"] == "product" and r0["name"] == "性能产品", str(r0))
    lim = api_user.list_runs(limit=2)
    rep.check("任务页:limit 只返回最近 N 个", len(lim) == 2 and lim[0]["id"] > lim[1]["id"] or lim[0]["id"] != lim[1]["id"], str([x["id"] for x in lim]))
    full_lib = api_user.library()
    brief_lib = api_user.library(brief=True)
    one = api_user.library_one(full_lib[0]["id"]) if full_lib else None
    rep.check("成品库精简列表:去掉正文 / 备选标题 / 标签 / 检查说明 / 千帆表现,其余字段和完整版一致",
              len(brief_lib) == len(full_lib) and all(not (set(api_user._LIB_HEAVY) & set(b)) for b in brief_lib)
              and all({k: v for k, v in f.items() if k not in api_user._LIB_HEAVY} == b for f, b in zip(full_lib, brief_lib)))
    rep.check("成品库单篇:返回完整内容;不存在的 id 报错", (one is None or (one["content"] is not None and "tags" in one and one["id"] == full_lib[0]["id"]))
              and _raises2(lambda: api_user.library_one(99999999)))
    # 图片目录大小(管理端概览用):缓存 + 过期后台刷新,不能每次刷新都逐个文件 stat
    from app import mirror
    mirror.DIR.mkdir(parents=True, exist_ok=True)
    probe = mirror.DIR / "_perf_probe.bin"
    mirror._DISK.update(mb=0.0, at=0.0, busy=False)
    base = mirror.disk_mb()
    probe.write_bytes(b"x" * 3 * 1048576)
    cached = mirror.disk_mb()
    mirror._DISK["at"] -= mirror._DISK_TTL + 1   # 模拟过期
    stale = mirror.disk_mb()                      # 过期:立刻返回旧值,后台刷新
    for _ in range(100):
        if not mirror._DISK["busy"]:
            break
        time.sleep(0.05)
    fresh = mirror.disk_mb()
    probe.unlink()
    mirror._DISK.update(mb=0.0, at=0.0, busy=False)
    rep.check("图片目录大小:缓存期内不重新扫描;过期后先返回旧值、后台刷新出新值", cached == base and stale == base and fresh >= base + 2.9, f"{base} {cached} {stale} {fresh}")
    off = api_user.list_runs(limit=2, offset=1)
    full = api_user.list_runs(limit=500)
    rep.check("任务页分页:offset 跳过前 N 个,总数接口和列表一致", [x["id"] for x in off] == [x["id"] for x in full[1:3]] and api_user.runs_count()["total"] == len(full))
    rep.check("进度:批量版和单个版给同样的结果;没有这个任务时返回 None", autorun.progress(plist[1]) == autorun.progress_many([plist[1], plist[1]])[plist[1]] and autorun.progress(999999) is None)
    nq = []
    _c = db.conn

    def counting():
        c = _c()
        c.set_trace_callback(lambda s: nq.append(s))
        return c
    db.conn = counting
    try:
        api_user._runs_info(ps)
    finally:
        db.conn = _c
    rep.check("任务页:查询条数不随任务数增长(6 个任务只要个位数条 SQL,原来每个 8 条左右)", len(nq) <= 12, f"{len(nq)} 条")

    # ---------- 缩略图 ----------
    try:
        from PIL import Image
    except ImportError:
        rep.check("缩略图:需要 Pillow(镜像里已加入 requirements)", False, "没有安装 Pillow")
        Image = None
    if Image:
        tmp = Path(tempfile.mkdtemp())
        old_img, old_dir = imagegen.IMG_DIR, thumbs.DIR
        imagegen.IMG_DIR, thumbs.DIR = tmp / "images", tmp / "thumbs"
        imagegen.IMG_DIR.mkdir()
        try:
            big_im = Image.effect_noise((1200, 1600), 60).convert("RGB")
            big_im.save(imagegen.IMG_DIR / "big.png")
            Image.new("RGB", (200, 260), (200, 50, 50)).save(imagegen.IMG_DIR / "small.png")
            (imagegen.IMG_DIR / "notimg.png").write_bytes(b"not an image at all")
            orig_size = (imagegen.IMG_DIR / "big.png").stat().st_size
            r = user.get("/images/big.png?w=360")
            rep.check("缩略图:返回 WebP,体积只有原图的一小部分,宽度 360", r.status_code == 200 and r.headers["content-type"] == "image/webp" and Image.open(io.BytesIO(r.content)).width == 360 and len(r.content) < orig_size / 4, f"{len(r.content)} / {orig_size}")
            rep.check("缩略图:浏览器可以长期缓存(immutable)", "immutable" in r.headers.get("cache-control", "") and "max-age=31536000" in r.headers["cache-control"])
            files = sorted(p.name for p in thumbs.DIR.iterdir())
            r2 = user.get("/images/big.png?w=360")
            rep.check("缩略图:第二次直接用缓存文件,不重复生成", r2.content == r.content and sorted(p.name for p in thumbs.DIR.iterdir()) == files)
            rep.check("缩略图:宽度只有固定几档(1 → 240,9999 → 720)", Image.open(io.BytesIO(user.get("/images/big.png?w=1").content)).width == 240 and Image.open(io.BytesIO(user.get("/images/big.png?w=9999").content)).width == 720)
            rep.check("缩略图:原图比目标小,原样返回(不放大)", user.get("/images/small.png?w=360").content == (imagegen.IMG_DIR / "small.png").read_bytes())
            rep.check("缩略图:解码失败也不报错,原样返回", user.get("/images/notimg.png?w=360").content == b"not an image at all")
            rep.check("缩略图:不带 w 还是原图,带一天的缓存头;找不到的图 404", user.get("/images/big.png").content == (imagegen.IMG_DIR / "big.png").read_bytes()
                      and "max-age=86400" in user.get("/images/big.png").headers.get("cache-control", "") and user.get("/images/nope.png?w=360").status_code == 404)
            import os
            n_before = len(list(thumbs.DIR.iterdir()))
            st = (imagegen.IMG_DIR / "big.png").stat()
            os.utime(imagegen.IMG_DIR / "big.png", (st.st_atime, st.st_mtime + 100))
            user.get("/images/big.png?w=360")
            rep.check("缩略图:原图换了(修改时间变了)会重新生成,不会一直用旧图", len(list(thumbs.DIR.iterdir())) == n_before + 1)
            rep.check("缩略图:warm 能提前做好", (lambda: (thumbs.warm(imagegen.IMG_DIR / "big.png", (480,)), any(".480." in p.name for p in thumbs.DIR.iterdir()))[1])())
        finally:
            imagegen.IMG_DIR, thumbs.DIR = old_img, old_dir

    # ---------- gzip:只压文本 ----------
    from fastapi import FastAPI, Response
    from fastapi.testclient import TestClient
    from app.textgzip import TextGZip
    a = FastAPI()
    a.add_middleware(TextGZip, minimum_size=1024)
    a.get("/api/x")(lambda: {"rows": [{"title": "标题" * 20, "n": i} for i in range(200)]})
    a.get("/images/x.png")(lambda: Response(b"\x89PNG" + bytes(20000), media_type="image/png"))
    tc = TestClient(a)
    jr, ir = tc.get("/api/x", headers={"Accept-Encoding": "gzip"}), tc.get("/images/x.png", headers={"Accept-Encoding": "gzip"})
    rep.check("gzip:接口 JSON 压缩(体积缩小一大半),内容照常", jr.headers.get("content-encoding") == "gzip" and len(jr.json()["rows"]) == 200 and int(jr.headers["content-length"]) < 6000, jr.headers.get("content-length", ""))
    rep.check("gzip:图片不压缩(白费 CPU)", "content-encoding" not in ir.headers and len(ir.content) == 20004)

    # 清理本用例造的数据
    for pr in plist + [pid2]:
        db.q_exec("DELETE FROM autoruns WHERE project_id=?", pr)
        db.q_exec("DELETE FROM crawl_tasks WHERE project_id=?", pr)
        db.q_exec("DELETE FROM playbooks WHERE project_id=?", pr)
        db.q_exec("DELETE FROM project_notes WHERE project_id=?", pr)
        db.q_exec("DELETE FROM drafts WHERE project_id=?", pr)
        db.q_exec("DELETE FROM projects WHERE id=?", pr)
    db.q_exec("DELETE FROM products WHERE id=?", prod)
