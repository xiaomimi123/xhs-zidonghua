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
    # 版本号:没有 version.json 显示 dev;有就原样给出;文件坏了也不报错
    from app import version as ver
    old_file, tmpf = ver.FILE, Path(tempfile.mkdtemp()) / "version.json"
    try:
        ver.FILE = tmpf
        v0 = api_user.version_info()
        tmpf.write_text(json.dumps({"version": "v1.99", "commit": "abc1234", "dirty": True, "built": "2026-11-13 10:00", "subject": "测试"}, ensure_ascii=False), encoding="utf-8")
        v1 = api_user.version_info()
        tmpf.write_text("{坏的", encoding="utf-8")
        v2 = api_user.version_info()
    finally:
        ver.FILE = old_file
    rep.check("版本号:没有文件 = dev;有文件原样给出;文件损坏也不报错", v0["version"] == "dev" and v1["version"] == "v1.99" and v1["commit"] == "abc1234" and v1["dirty"] is True and v2["version"] == "dev", str((v0, v1, v2)))
    # 采集任务租约:心跳续租有上限(领走后又丢掉的任务不能被永远续租);超时退回累计次数,到上限标失败并通知达人库
    from types import SimpleNamespace
    from app import api_collector as ac
    fake = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    ids = []
    with db.conn() as c:
        mk_t = lambda q, started, claimed, tries=0, purpose="qianfan", params="{}": c.execute(
            "INSERT INTO crawl_tasks(type,query,status,worker,purpose,claim_start,claimed_at,tries,params) VALUES('qianfan',?,'running','lease-w',?,datetime('now',?),datetime('now',?),?,?)",
            (q, purpose, started, claimed, tries, params)).lastrowid
        t_old = mk_t("buyer_contact", "-30 minutes", "-2 minutes")    # 领走 30 分钟了:超过 10 分钟上限,不再续租
        t_new = mk_t("notes", "-2 minutes", "-2 minutes")              # 刚领走:正常续租
        t_lib = mk_t("kw", "-30 minutes", "-2 minutes", purpose="library")   # 沉淀任务上限 60 分钟:30 分钟还续租
        ids += [t_old, t_new, t_lib]
        before = {i: c.execute("SELECT claimed_at FROM crawl_tasks WHERE id=?", (i,)).fetchone()[0] for i in ids}
        ac._heartbeat(c, fake, "lease-w", 0)
        after = {i: c.execute("SELECT claimed_at FROM crawl_tasks WHERE id=?", (i,)).fetchone()[0] for i in ids}
    rep.check("采集租约:千帆任务领走超过 10 分钟后心跳不再续租;刚领走的、沉淀任务照常续租", after[t_old] == before[t_old] and after[t_new] > before[t_new] and after[t_lib] > before[t_lib], str((before, after)))
    with db.conn() as c:
        c.execute("INSERT OR REPLACE INTO kol_creators(creator_id,name,contact_status) VALUES('lease-cid','租约测试','queued')")
        t_a = mk_t("buyer_contact", "-60 minutes", "-20 minutes", tries=0)
        t_b = mk_t("buyer_contact", "-60 minutes", "-20 minutes", tries=ac.MAX_TRIES - 1, params='{"creator_id": "lease-cid"}')
        ids += [t_a, t_b]
        ac._expire_stale(c)
        ra = c.execute("SELECT status, tries, worker FROM crawl_tasks WHERE id=?", (t_a,)).fetchone()
        rb = c.execute("SELECT status, error FROM crawl_tasks WHERE id=?", (t_b,)).fetchone()
        ks = c.execute("SELECT contact_status FROM kol_creators WHERE creator_id='lease-cid'").fetchone()[0]
        still = c.execute("SELECT status FROM crawl_tasks WHERE id=?", (t_new,)).fetchone()[0]
    rep.check("采集租约:超时的任务退回待领取并 tries+1;退回满次数标失败、达人库显示出错;没超时的不动",
              ra["status"] == "pending" and ra["tries"] == 1 and ra["worker"] == "" and rb["status"] == "failed" and "没有回传" in rb["error"] and ks == "error" and still == "running", str((dict(ra), dict(rb), ks, still)))
    with db.conn() as c:
        c.execute(f"DELETE FROM crawl_tasks WHERE id IN ({','.join(str(i) for i in ids)})")
        c.execute("DELETE FROM kol_creators WHERE creator_id='lease-cid'")
    # 场景 + 拍法:一批里先把不同场景用一遍;用过的(场景, 拍法)会被避开
    from app import covers
    import random as _r
    sc7 = covers.scene_pool("美食", None)
    pk = covers.pick_looks(len(sc7), sc7, None, _r.Random(1))
    only = sc7[0]
    used_ = {(only, lk): 1 for lk in covers.LOOKS[:170]}
    pk2 = covers.pick_looks(10, [only], used_, _r.Random(2))
    pk6 = covers.pick_looks(6, sc7, None, _r.Random(7))
    parts = [x[1].split(";") for x in pk6]
    rep.check("拍法:连着挑 6 张,机位 / 光线 / 画面状态三项都在换(不是只换场景)", len({p_[0] for p_ in parts}) == 6 and len({p_[1] for p_ in parts}) >= 5 and len({p_[2] for p_ in parts}) == 6, str(parts))
    rep.check("场景池 + 拍法:一批里每个场景先用一遍;拍法一共 180 种且互不重复;用过的拍法被避开",
              len({x[0] for x in pk}) == len(sc7) and len(covers.LOOKS) == len(set(covers.LOOKS)) == 180
              and {x[1] for x in pk2} == set(covers.LOOKS[170:]) and len(sc7) >= 6, str((len(sc7), len({x[0] for x in pk}))))
    # ---- 产品拍摄规格 + 出图规则(去掉冒烟 / 纸巾 / 杂乱 / 自相矛盾的场景叠加)----
    import random as _rr
    from app import covers as cv, llm as llm_, products as pr_, worker as wk_
    banned = ("冒着热气", "纸巾", "杂乱", "没收拾", "随手放着", "食材随手放着", "杂物随意堆放", "刚做好")
    allsc = list(cv.SCENES.values()) + list(cv.GENERIC_SCENES)
    rep.check("场景库:不再含冒热气 / 纸巾 / 杂乱 / 没收拾 / 食材随手放这类词", not [t for t in allsc for w_ in banned if w_ in t], str([t for t in allsc if any(w_ in t for w_ in banned)]))
    st = [cv.ugc_style(_rr.Random(i)) for i in range(400)]
    share = sum("随手拍的痕迹" in x for x in st) / 400
    rep.check("生图风格:整洁自然为主,不画热气 / 成团纸巾 / 杂物堆;约三分之一的图带一点随手拍的痕迹",
              all("不要出现烟雾、蒸汽或热气" in x and "手机随手拍的生活照风格" in x for x in st) and 0.2 < share < 0.5 and "生活杂物" not in cv.UGC_STYLE, str(share))
    jerky = {"name": "原切猪肉脯", "profile": {"category": "猪肉脯", "scenes": ["办公室下午茶零食", "追剧解馋零食"]}}
    rep.check("产品类型:「下午茶零食」不再被当成冲饮;AI 规格里的类型优先(腐乳 = 下饭佐餐)",
              cv.product_kind(jerky) == "snack" and cv.product_kind({"name": "腐乳", "profile": {"scenes": ["早餐佐餐"], "shoot": {"kind": "side"}}}) == "side"
              and "coffee_corner" not in cv.KIND_POOLS["side"] and cv.product_kind({"name": "x", "profile": {"shoot": {"kind": "乱写"}}}) != "乱写")
    prs = [{"name": "白粥", "freq": "high"}, {"name": "拌面", "freq": "mid"}, {"name": "夹馒头", "freq": "low"}]
    picks = cv.pick_pairings(300, prs, None, _rr.Random(3))
    rep.check("搭配轮换:少用的搭配(馒头)很少出现,常用的多,但不会总是同一个", picks.count("夹馒头") / 300 < 0.15 and picks.count("白粥") > picks.count("夹馒头") * 2 and len(set(picks[:12])) >= 2
              and cv.pick_pairings(3, [], None) == ["", "", ""], str({k: picks.count(k) for k in set(picks)}))
    used_b = cv.pick_pairings(40, prs, {"白粥": 200}, _rr.Random(4))
    rep.check("搭配轮换:这个产品以前用过很多的搭配会被避开", used_b.count("白粥") < used_b.count("拌面"), str({k: used_b.count(k) for k in set(used_b)}))
    old_chat = llm_.chat
    try:
        llm_.chat = lambda system, user, temperature=1.0: {"kind": "乱写", "serve": "cold", "pairings": [{"name": "白粥", "freq": "high"}, {"name": "夹馒头", "freq": "超低"}, {"name": ""}, "拌面"],
                                                           "avoid": ["烟雾蒸汽", "", "别的品牌", "a", "b", "c", "d", "e"], "shoot_note": "x" * 100}
        sp = llm_.shoot_spec("腐乳", "豆腐乳")
    finally:
        llm_.chat = old_chat
    rep.check("拍摄规格:模型给的内容被整理成固定格式(类型不合规丢掉、频率不合规当偶尔、avoid 最多 6 条)",
              sp["kind"] == "" and sp["serve"] == "cold" and [x["name"] for x in sp["pairings"]] == ["白粥", "夹馒头", "拌面"] and sp["pairings"][1]["freq"] == "mid"
              and len(sp["avoid"]) == 6 and len(sp["shoot_note"]) == 60, str(sp))
    spec_ok = {"kind": "side", "serve": "room", "pairings": prs, "avoid": ["别的品牌豆腐乳", "姜蒜"], "shoot_note": "瓶子放在饭桌上"}
    rep.check("拍摄规格:画像里有就直接用(不再调 AI);没有且调用失败返回空规格;生成的会缓存",
              pr_.get_spec({"id": 1, "name": "x", "profile": {"shoot": spec_ok}}) == spec_ok and pr_.get_spec({"id": 1, "name": "x", "profile": {}}, generate=False) == {})
    saved, old_ss, old_sv = [], llm_.shoot_spec, pr_.save_spec
    try:
        llm_.shoot_spec = lambda *a_, **k_: spec_ok
        pr_.save_spec = lambda pid_, sp_: saved.append((pid_, sp_))
        pp = {"id": 7, "name": "腐乳", "profile": {}}
        got = pr_.get_spec(pp)
        llm_.shoot_spec = lambda *a_, **k_: (_ for _ in ()).throw(RuntimeError("没有 Key"))
        got2 = pr_.get_spec({"id": 8, "name": "x", "profile": {}})
    finally:
        llm_.shoot_spec, pr_.save_spec = old_ss, old_sv
    rep.check("拍摄规格:生成后缓存到产品画像;AI 调用失败返回空且不缓存", got == spec_ok and saved == [(7, spec_ok)] and pp["profile"]["shoot"] == spec_ok and got2 == {} and len(saved) == 1)
    blk = pr_.block({"id": 1, "name": "腐乳", "profile": {"shoot": spec_ok}})
    rep.check("写笔记的产品资料里带上拍摄规格:搭配和频率 / 不要出现 / 冷热 / 少用整篇最多一张",
              "夹馒头(少用,整篇最多一张)" in blk and "白粥(常用)" in blk and "姜蒜" in blk and "不要冒" not in blk and "常温" in blk and "最多一张" in blk and "纸巾团" in blk, blk[-400:])
    rep.check("「不要出现」不重复:规格里已经写了热气 / 蒸汽 / 冒烟就不再自动补;热饮不补;没有规格返回空串",
              pr_.avoid_text({"serve": "room", "avoid": ["热气蒸汽", "姜蒜"]}) == "不要出现:热气蒸汽、姜蒜" and pr_.avoid_text({"serve": "hot", "avoid": ["姜蒜"]}) == "不要出现:姜蒜" and pr_.avoid_text({}) == "")
    ip = wk_._image_prompt("标题", "餐桌上一碗白粥配一小碟腐乳", True, "", pr_.avoid_text(spec_ok))
    rep.check("生图提示词:带上「不要出现」(规格里的 + 不是热饮就不要热气),不再自带杂物", "不要出现:别的品牌豆腐乳、姜蒜、热气和蒸汽" in ip and "生活杂物" not in ip and "场景:" not in ip, ip[:240])
    # ---- 上传产品时的素材检查 ----
    import base64 as _b64
    from app import product_check as pc, products as pdt
    from PIL import Image as _Im

    def _png(color, size=(800, 1000)):
        buf = io.BytesIO()
        _Im.new("RGB", size, color).save(buf, "PNG")
        return buf.getvalue()

    du = lambda b: "data:image/png;base64," + _b64.b64encode(b).decode()
    img_a, img_b, img_c, img_d, img_small = _png((200, 30, 30)), _png((30, 200, 30)), _png((30, 30, 200)), _png((200, 200, 30)), _png((90, 90, 90), (300, 300))
    pid_cin = pdt.create("肉桂粉(检查用)", [du(img_a)], selling_points="锡兰肉桂,香气温润")
    db.q_exec("UPDATE products SET category='肉桂粉' WHERE id=?", pid_cin)
    ws = pc.check("姜黄粉(检查用)", [(img_a, "png"), (img_b, "png"), (img_b, "png"), (img_c, "png"), (img_small, "png")], None,
                  "姜黄 + 肉桂经典搭配\n**口感风味**", "帮我收集一些家庭主妇、中年人", "", 0, vision=False)
    msgs = [w["msg"] for w in ws]
    rep.check("素材检查:图和别的产品里的一样 / 上传的图自己重复 / 分辨率偏低 / 超过 3 张只取前 3 张,都提醒出来",
              any("第 1 张图和产品「肉桂粉(检查用)」里的图完全一样" in m for m in msgs) and any("第 3 张图和第 2 张重复" in m for m in msgs)
              and any("第 5 张图分辨率偏低" in m for m in msgs) and any("只会取前 3 张" in m for m in msgs), str(msgs))
    rep.check("素材检查:文案里有别的产品的关键词(肉桂)/ 像给 AI 的指令 / 带格式符号,都提醒;warn 排在 tip 前面",
              any("另一个产品「肉桂粉(检查用)」的关键词「肉桂」" in m for m in msgs) and any("像是给 AI 的指令" in m for m in msgs)
              and any("格式符号" in m for m in msgs) and [w["level"] for w in ws] == sorted([w["level"] for w in ws], key=lambda x: 0 if x == "warn" else 1), str(msgs))
    long_ws = pc.check("腐乳", [(img_d, "png")], None, "卖点" * 300, "人群" * 80, "", 0, vision=False)
    empty_ws = pc.check("腐乳", [(img_d, "png")], None, "", "", "", 0, vision=False)
    ok_ws = pc.check("国圣腐乳", [(img_d, "png")], None, "米酱发酵,咸香入味 / 配白粥拌面", "家庭主妇、中老年人", "治疗、功效", 0, vision=False)
    rep.check("素材检查:文案太长会被截断的提醒;没填卖点给建议;规范的素材不报问题",
              any("「卖点」有 600 字" in w["msg"] for w in long_ws) and any("「适合人群」有 160 字" in w["msg"] for w in long_ws)
              and any(w["level"] == "tip" and "没有填卖点" in w["msg"] for w in empty_ws) and ok_ws == [], str((long_ws, empty_ws, ok_ws)))
    edit_ws = pc.check("肉桂粉(检查用)", [(img_a, "png")], None, "", "", "", pid_cin, vision=False)   # 编辑自己:不和自己的图比
    rep.check("素材检查:编辑产品时不把自己的图当成「重复」", not any("完全一样" in w["msg"] for w in edit_ws), str(edit_ws))
    old_vc = llm_.vision_chat
    try:
        llm_.vision_chat = lambda system, text, images, **k_: '{"issues": [{"index": 2, "reason": "是肉桂粉的罐子"}, {"index": 9, "reason": "越界的不要"}, {"index": "x"}]}'
        vws = pc.check("姜黄粉", [(img_a, "png"), (img_b, "png")], None, "姜黄冲饮", "上班族", "", 0, vision=True)
        llm_.vision_chat = lambda *a_, **k_: (_ for _ in ()).throw(RuntimeError("没配看图接口"))
        vws2 = pc.check("姜黄粉", [(img_a, "png")], None, "姜黄冲饮", "上班族", "", 0, vision=True)
    finally:
        llm_.vision_chat = old_vc
    rep.check("素材检查:看图模型指出哪张不是这个产品就提醒(越界序号忽略);看图出错 / 没配接口时不影响其他检查",
              [w["msg"] for w in vws if "可能不是这个产品" in w["msg"]] == ["第 2 张图可能不是这个产品:是肉桂粉的罐子"] and not any("可能不是" in w["msg"] for w in vws2))
    ep = api_user.check_product(api_user.ProductCheckIn(name="姜黄粉", images=[du(img_a)], selling_points="x", audience="上班族"))
    rep.check("素材检查接口:返回提醒;图片格式不对返回 400", any("完全一样" in w["msg"] for w in ep["warnings"]) and _raises2(lambda: api_user.check_product(api_user.ProductCheckIn(name="x", images=["不是图片"]))))
    pdt.delete(pid_cin)
    # ---- 产品图按页面顺序保存(拖动 / ‹ › 调整顺序;原图和新图可以交错)----
    pid_o = pdt.create("排序测试", [du(img_a), du(img_b), du(img_c)])
    o0 = pdt.get(pid_o)["images"]
    db.q_exec("UPDATE products SET profile=? WHERE id=?", json.dumps({"category": "测试", "shoot": {"kind": "side"}}), pid_o)
    nm = lambda **k: dict(name="排序测试", price="", selling_points="", audience="", forbid="", **k)
    pdt.update(pid_o, keep=o0, add=None, order=[f"k:{o0[2]}", f"k:{o0[0]}", f"k:{o0[1]}"], **nm())
    o1 = pdt.get(pid_o)
    rep.check("产品图顺序:只调整顺序时按页面顺序保存,识别结果(含拍摄规格)不清",
              o1["images"] == [o0[2], o0[0], o0[1]] and o1["profile"].get("shoot", {}).get("kind") == "side" and o1["profile"].get("category") == "测试", str(o1["images"]))
    pdt.update(pid_o, keep=[o0[2], o0[0], o0[1]], add=[du(img_d)], order=[f"k:{o0[2]}", "a:0", f"k:{o0[0]}", f"k:{o0[1]}"], **nm())
    o2 = pdt.get(pid_o)
    rep.check("产品图顺序:新上传的图可以插在原图中间;图的构成变了就清掉识别结果",
              len(o2["images"]) == 4 and o2["images"][0] == o0[2] and o2["images"][2:] == [o0[0], o0[1]] and o2["images"][1] not in o0 and not o2["profile"], str(o2["images"]))
    pdt.update(pid_o, keep=[o0[2], o0[0]], add=None, order=["k:不存在.jpg", "a:9", f"k:{o0[0]}", f"k:{o0[0]}", "乱写"], **nm())
    o3 = pdt.get(pid_o)
    rep.check("产品图顺序:order 里不认识的、重复的忽略,没提到的图补在后面,不会丢图;没传 order 保持原来的顺序",
              o3["images"] == [o0[0], o0[2]] and not (pdt.DIR / o0[1]).exists(), str(o3["images"]))
    pdt.update(pid_o, keep=[o0[2], o0[0]], add=[du(img_b)], order=None, **nm())
    o4 = pdt.get(pid_o)["images"]
    rep.check("产品图顺序:不传 order = 原图在前(按原来的顺序)、新图在后", o4[:2] == [o0[0], o0[2]] and len(o4) == 3, str(o4))
    other_png = pdt.get(pid_o)["images"][0]
    pid_p = pdt.create("排序测试2", [du(img_c)])
    ws_o = pc.check("排序测试2", [(img_a, "png")], [pdt.get(pid_p)["images"][0]], "x", "上班族", "", pid_p, vision=False, order=["a:0", "k:" + pdt.get(pid_p)["images"][0]])
    rep.check("素材检查:提示里的「第几张」按页面顺序算(新图拖到了第 1 位)", any("第 1 张图和产品「排序测试」里的图完全一样" in w["msg"] for w in ws_o), str([w["msg"] for w in ws_o]))
    pdt.delete(pid_o)
    pdt.delete(pid_p)
    # ---- 任务发起人:任务列表 / 详情 / 管理端采集监控都能看到是谁下发的,并能按人筛选 ----
    from app import api_admin as aa_, auth as auth_, kol as kol_
    db.q_exec("INSERT INTO staff(username, name, password_hash) VALUES('t_by_a', '发起人甲', 'x')")
    mkp = lambda cb: db.q_exec("INSERT INTO projects(track, keywords, created_by) VALUES('美食', '[]', ?)", cb).lastrowid
    p_a, p_adm, p_ghost = mkp("t_by_a"), mkp(""), mkp("已删员工")
    for pid_ in (p_a, p_adm, p_ghost):
        db.q_exec("INSERT INTO autoruns(project_id, status, target) VALUES(?, 'done', 1)", pid_)
    allr = {r["id"]: r for r in api_user.list_runs(limit=500)}
    rep.check("任务发起人:员工显示姓名、管理员发起的显示「管理员」、员工被删了显示登录名", allr[p_a]["by"] == "发起人甲" and allr[p_adm]["by"] == "管理员" and allr[p_ghost]["by"] == "已删员工")
    only = api_user.list_runs(limit=500, by="发起人甲")
    cnt = api_user.runs_count(by="发起人甲")
    cnt_all = api_user.runs_count()
    rep.check("任务发起人:按人筛选只返回他的任务;总数跟着筛选走,各发起人的任务数不受筛选影响",
              [r["id"] for r in only] == [p_a] and cnt["total"] == 1 and cnt["who"]["发起人甲"] == 1 and cnt["who"]["管理员"] >= 1
              and cnt_all["total"] == sum(cnt_all["who"].values()) and cnt_all["who"] == cnt["who"], str((cnt, cnt_all)))
    mkt = lambda **k: db.q_exec("INSERT INTO crawl_tasks(type, query, status, project_id, purpose, created_by) VALUES(?, ?, 'pending', ?, ?, ?)",
                                k.get("type", "keyword"), k["q"], k.get("pid"), k.get("purpose", "project"), k.get("cb", "")).lastrowid
    t_proj, t_lib, t_qc, t_qs, t_adm = (mkt(q="项目词", pid=p_a), mkt(q="沉淀词", purpose="library"), mkt(type="qianfan", q="buyer_contact", purpose="qianfan", cb="t_by_a"),
                                        mkt(type="qianfan", q="notes", purpose="qianfan"), mkt(q="管理员项目词", pid=p_adm))
    tk = {r["id"]: r["by"] for r in aa_.all_tasks(limit=1000)}
    rep.check("采集监控发起人:项目任务看项目创建人;沉淀任务 = 管理员;达人库获取联系方式看记下的人;千帆定时同步 = 系统",
              tk[t_proj] == "发起人甲" and tk[t_lib] == "管理员" and tk[t_qc] == "发起人甲" and tk[t_qs] == "系统" and tk[t_adm] == "管理员", str(tk))
    by_a = [r["id"] for r in aa_.all_tasks(by="发起人甲", limit=1000)]
    who_t = aa_.tasks_who()
    rep.check("采集监控发起人:按人筛选;各发起人的任务数(筛选按钮用)", set(by_a) == {t_proj, t_qc} and who_t["发起人甲"] == 2 and "系统" in who_t and "管理员" in who_t, str(who_t))
    db.q_exec("INSERT OR REPLACE INTO kol_creators(creator_id, name, fans) VALUES('t_by_cid', '联系方式测试', 1000)")
    old_en = db.get_settings().get("kol_contact_enabled")
    db.save_settings({"kol_contact_enabled": "1"})
    tok = auth_.CURRENT.set({"username": "t_by_a", "id": 999, "name": "发起人甲"})
    try:
        kol_.request_contact(db.q_one("SELECT id FROM kol_creators WHERE creator_id='t_by_cid'")["id"])
    finally:
        auth_.CURRENT.reset(tok)
        db.save_settings({"kol_contact_enabled": old_en or "0"})
    rc = db.q_one("SELECT created_by FROM crawl_tasks WHERE query='buyer_contact' AND label='联系方式测试' ORDER BY id DESC")
    rep.check("达人库获取联系方式:任务上记下是谁点的", rc and rc["created_by"] == "t_by_a", str(rc))
    db.q_exec("DELETE FROM crawl_tasks WHERE label='联系方式测试' OR id IN (?,?,?,?,?)", t_proj, t_lib, t_qc, t_qs, t_adm)
    db.q_exec("DELETE FROM kol_creators WHERE creator_id='t_by_cid'")
    db.q_exec("DELETE FROM autoruns WHERE project_id IN (?,?,?)", p_a, p_adm, p_ghost)
    db.q_exec("DELETE FROM projects WHERE id IN (?,?,?)", p_a, p_adm, p_ghost)
    db.q_exec("DELETE FROM staff WHERE username='t_by_a'")
    # ---- 场景模板扩充 / AI 专属场景 / 产品实际大小 / 新建产品接口 / 场景下拉按产品给 ----
    from app import api_live as al_, covers as cv3
    bad_w = ("冒着热气", "纸巾", "杂乱", "没收拾", "随手放着", "刚做好", "水渍")
    rep.check("场景库:34 种模板;每个类型的场景池里的键都存在、没有重复;所有模板都不含冒热气 / 纸巾 / 杂乱这类词;换背景页的备选保持精简(14 种)",
              len(cv3.SCENES) == 34 and all(k in cv3.SCENES and len(v) == len(set(v)) for v in cv3.KIND_POOLS.values() for k in v)
              and not [t for t in cv3.SCENES.values() if any(w_ in t for w_ in bad_w)] and len(cv3.BG_SCENES) == 14 and min(len(v) for v in cv3.KIND_POOLS.values()) >= 11)
    keys_all = list(cv3.SCENES)
    ok_step = True
    for n_ in (4, 6, 7, 10, 12, 14, 15, 21):   # 场景池大小是 7、14、21 这种和旧步长 7 不互质的数时,原来同一篇里每张都是同一个场景
        cv3.KIND_POOLS["tmpk"] = keys_all[:min(n_, len(keys_all))] if n_ <= len(keys_all) else keys_all
        pk_ = {"name": "x", "profile": {"shoot": {"kind": "tmpk"}}}
        ok_step = ok_step and all(len({cv3.scene("美食", d_, k, pk_) for k in range(1, 5)}) == 4 for d_ in range(0, 12))
    cv3.KIND_POOLS.pop("tmpk", None)
    rep.check("场景挑选:场景池大小是 7 / 14 / 21 这种和旧步长不互质的数时,同一篇里连着的几张也不会重复", ok_step)
    own = ["窗边的小圆桌上放着一杯燕麦奶和一本书", "办公室午休时的工位,手边是笔记本和水杯"]
    pool_ai = cv3.scene_pool("美食", {"name": "x", "profile": {"shoot": {"kind": "drink", "scenes": own}}})
    rep.check("场景池:AI 为这个产品推荐的专属场景排在前面,后面是这个类型的模板场景,不重复", pool_ai[:2] == own and len(pool_ai) == 2 + len(cv3.KIND_POOLS["drink"]) and len(set(pool_ai)) == len(pool_ai))
    old_chat3 = llm_.chat
    try:
        llm_.chat = lambda system, user, temperature=1.0: {"kind": "side", "serve": "room", "pairings": [], "avoid": [], "shoot_note": "",
                                                           "scenes": ["餐桌上一碗白粥配一瓶腐乳,窗边自然光", "桌面有纸巾和水渍的餐桌", "短", "餐桌上一碗白粥配一瓶腐乳,窗边自然光", "办公室午休,桌上放着饭盒和一瓶腐乳"] + ["场景%d,在某处放着东西" % i for i in range(10)],
                                                           "size": "玻璃罐高约 9cm、直径 6cm,单手可握"}
        sp3 = llm_.shoot_spec("腐乳", "豆腐乳")
    finally:
        llm_.chat = old_chat3
    rep.check("拍摄规格:专属场景整理(带纸巾 / 水渍的丢掉、太短的丢掉、重复的去重、最多 8 个),带上实际大小",
              len(sp3["scenes"]) == 8 and "餐桌上一碗白粥配一瓶腐乳,窗边自然光" in sp3["scenes"] and not any("纸巾" in x for x in sp3["scenes"]) and sp3["scenes"].count("餐桌上一碗白粥配一瓶腐乳,窗边自然光") == 1
              and sp3["size"].startswith("玻璃罐高约 9cm"), str(sp3))
    mk_sc = lambda kind_: {"kind": kind_, "serve": "room", "pairings": [{"name": "夹馒头", "freq": "low"}, {"name": "配白粥", "freq": "high"}], "avoid": [], "shoot_note": "",
                           "scenes": ["户外野餐桌,馒头、瓶子、野餐篮", "办公室茶水间,便当盒、餐巾、一瓶腐乳", "厨房灶台,面条碗、调料架、一瓶腐乳", "午餐饭桌,白米饭、竹筷、一小碟腐乳", "家庭早餐桌,白粥配腐乳、木质托盘"], "size": "x"}
    old_chat4 = llm_.chat
    try:
        llm_.chat = lambda system, user, temperature=1.0: mk_sc("side")
        sp_side = llm_.shoot_spec("腐乳", "豆腐乳")
        llm_.chat = lambda system, user, temperature=1.0: mk_sc("cook")
        sp_cook = llm_.shoot_spec("腐乳", "豆腐乳")
    finally:
        llm_.chat = old_chat4
    rep.check("拍摄规格·专属场景:带「少用」搭配的关键词(馒头)/ 餐巾 / 非烹饪类产品的灶台 都丢掉;烹饪调味类产品保留灶台",
              sp_side["scenes"] == ["午餐饭桌,白米饭、竹筷、一小碟腐乳", "家庭早餐桌,白粥配腐乳、木质托盘"] and "厨房灶台,面条碗、调料架、一瓶腐乳" in sp_cook["scenes"]
              and not any("餐巾" in x or "馒头" in x for x in sp_cook["scenes"]), str((sp_side["scenes"], sp_cook["scenes"])))
    pid_z = pdt.create("大小测试", [du(img_a)], size_note="罐高 8cm,单手可握")
    z0 = pdt.get(pid_z)
    db.q_exec("UPDATE products SET profile=? WHERE id=?", json.dumps({"shoot": {"kind": "drink", "size": "AI 估:罐高 10cm", "pairings": [], "avoid": []}}), pid_z)
    z1 = pdt.get(pid_z)
    pdt.update(pid_z, name="大小测试", keep=z1["images"], add=None, order=None)   # 不传 size_note = 不改
    z2 = pdt.get(pid_z)
    pdt.update(pid_z, name="大小测试", keep=z1["images"], add=None, order=None, size_note="")   # 传空 = 清掉,改用 AI 估的
    z3 = pdt.get(pid_z)
    rep.check("产品实际大小:用户填的优先于 AI 估的;更新时不传不改、传空清掉(改用 AI 估的);写进给大模型的产品资料",
              z0["size_note"] == "罐高 8cm,单手可握" and pdt.size_text(z1) == "罐高 8cm,单手可握" and z2["size_note"] == "罐高 8cm,单手可握" and z3["size_note"] == ""
              and pdt.size_text(z3) == "AI 估:罐高 10cm" and "罐高 8cm,单手可握" in pdt.block(z1) and "AI 估:罐高 10cm" in pdt.block(z3)
              and pdt.listing()[-1]["size_note"] in ("", "罐高 8cm,单手可握"))
    ip3 = wk_._image_prompt("", "一碗白粥配一瓶腐乳", True, "", "", "罐高 9cm")
    rep.check("笔记内页配图的提示词也带产品实际大小", "产品的真实大小:罐高 9cm" in ip3 and "真实比例" in ip3 and "真实大小" not in wk_._image_prompt("", "x", True, "", "", ""))
    r_new = api_user.create_product(api_user.ProductIn(name="接口新建", images=[du(img_b)], selling_points="x", size_note="高 5cm"))
    n_new = pdt.get(r_new["id"])
    rep.check("新建产品接口:返回产品 id,保存名称 / 图 / 实际大小;图片不合法返回 400", n_new["name"] == "接口新建" and n_new["size_note"] == "高 5cm" and len(n_new["images"]) == 1
              and _raises2(lambda: api_user.create_product(api_user.ProductIn(name="x", images=["不是图片"]))) and _raises2(lambda: api_user.create_product(api_user.ProductIn(name=" ", images=[du(img_c)]))))
    db.q_exec("UPDATE products SET profile=? WHERE id=?", json.dumps({"shoot": {"kind": "side", "size": "罐高约 9cm", "pairings": [{"name": "白粥", "freq": "high"}], "avoid": [], "scenes": own}}, ensure_ascii=False), pid_z)
    sc_z = al_.scenes(pid_z)
    rep.check("场景下拉按产品给:专属场景在前、类型名、AI 场景个数、实际大小(用户没填 = AI 估的)、搭配;不带产品是通用的;找不到产品 404",
              sc_z["scenes"][:2] == own and sc_z["kind"] == "下饭佐餐" and sc_z["ai"] == 2 and sc_z["size"] == "罐高约 9cm" and sc_z["size_user"] is False and sc_z["pairings"] == ["白粥"] and sc_z["ready"]
              and "scenes" in al_.scenes() and not al_.scenes()["scenes"][0] in own and _raises2(lambda: al_.scenes(99999999)))
    vwarn_old = llm_.vision_chat
    try:
        llm_.vision_chat = lambda system, text, images, **k_: '{"issues": [], "first_ok": false, "first_reason": "是手持近景,看不到完整包装"}'
        fw = pc.check("腐乳", [(img_a, "png")], None, "x", "上班族", "", 0, vision=True)
        llm_.vision_chat = lambda system, text, images, **k_: '{"issues": [], "first_ok": true}'
        fw2 = pc.check("腐乳", [(img_a, "png")], None, "x", "上班族", "", 0, vision=True)
    finally:
        llm_.vision_chat = vwarn_old
    rep.check("素材检查:第 1 张不是白底完整包装图时给建议(tip),是的不提示", any(w["level"] == "tip" and "第 1 张最好是白底" in w["msg"] and "看不到完整包装" in w["msg"] for w in fw) and not any("第 1 张最好" in w["msg"] for w in fw2), str(fw))
    for z_ in (pid_z, r_new["id"]):
        pdt.delete(z_)
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
