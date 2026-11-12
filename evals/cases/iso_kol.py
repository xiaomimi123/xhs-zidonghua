"""达人库:买手广场采集到的达人 → 规范化、分档、适配度、去重入库 → 筛选/排序/导出 → 建联状态和备注 → 任务下发。
结束时清掉自己造的数据和设置。"""
import json

from ..common import ROOT, _raises2


def run(rep, env):
    from app import api_kol, db, kol, qianfan as qf
    col, q, user, app = env.col, env.q, env.user, env.app
    app.include_router(api_kol.router)   # isolated 的最小应用里默认没挂达人库路由

    print("\n[达人库]")
    keep = {k: db.get_settings()[k] for k in ("kol_enabled", "kol_cats", "kol_targets", "kol_content", "kol_hours", "kol_pages", "qf_enabled", "qf_kinds", "collector_min_cv", "kol_contact_enabled", "kol_contact_cap", "kol_contact_paused")}
    TG, CT = "保健食品,咖啡/麦片/冲饮,传统滋补营养品,粮油调味,零食/坚果", "美食,健康养生,低脂低卡"
    db.save_settings({"kol_targets": TG, "kol_content": CT, "kol_cats": "", "kol_enabled": "0", "kol_pages": "5"})
    try:
        # 用真实接口回来的样本(名字、ID 已换成假的):规范化
        sample = json.loads((ROOT / "fixtures" / "kol_sample.json").read_text(encoding="utf-8"))
        a = kol.normalize(sample[0])
        rep.check("达人规范化(真实样本):量级、历史合作品类、平台标签编码翻成文字", a["tier"] == "头部" and "传统滋补营养品" in a["history_cats"] and a["coop_tags"] == ["积极合作中"] and a["fit"] == 2, str({k: a[k] for k in ("tier", "history_cats", "coop_tags", "fit")}))
        rep.check("达人规范化(真实样本):区间文字拆成可排序的数、指标转成数字", a["gmv_min"] > 0 and a["price_min"] > 0 and isinstance(a["avg_viewers"], float) and any(0 < kol.normalize(x)["active_rate"] <= 1 for x in sample), str((a["gmv_text"], a["gmv_min"], a["price_text"], a["avg_viewers"], a["active_rate"])))
        rep.check("达人规范化:不保存手机号和微信号", not any(k in a for k in ("phone", "telephone", "wechat", "raw")) and not any("tel" in k for k in a))
        raw_api = {"distributor_id": "d1", "distributor_name": "接口原名", "fans_num": 5000, "telephone": "ENC==", "phone": "138", "avg_sale_amount": "2w-5w", "atv": "100-500", "buyer_tag": ["4"]}
        b = kol.normalize(raw_api)
        rep.check("达人规范化:认接口原始字段名,忽略加密手机号", b["creator_id"] == "d1" and b["name"] == "接口原名" and b["coop_tags"] == ["邀约回复率高"] and "ENC==" not in str(b))
        rep.check("达人规范化:没有 ID 也没有名字的丢掉", kol.normalize({"fans": 5}) is None)
        rep.check("区间:500w以上 / 2000-5000 / 50w-100w", kol._range("500w以上") == (5000000.0, None) and kol._range("2000-5000") == (2000.0, 5000.0) and kol._range("50w-100w") == (500000.0, 1000000.0))
        rep.check("量级:头部 / 腰部 / 初级 / 素人", [kol.tier_of(n) for n in (2_000_000, 150_000, 20_000, 800)] == ["头部", "腰部", "初级", "素人"])
        tg, ct = kol.targets(), kol.content_targets()
        rep.check("适配度:带过同类品 = 高;只有内容相关 = 一般;都没有 = 低", kol.fit_of(["时尚"], ["保健食品/膳食营养补充食品"], [], tg, ct) == 2 and kol.fit_of(["美食"], ["女装/女士精品"], [], tg, ct) == 1
                  and kol.fit_of(["时尚"], ["女装/女士精品"], [], tg, ct) == 0 and kol.fit_of([], [], ["粮油调味/速食/干货/烘焙"], tg, ct) == 2)
        rep.check("采集品类:没填就用目标品类", kol.collect_cats() == ["保健食品", "咖啡/麦片/冲饮", "传统滋补营养品", "粮油调味", "零食/坚果"], str(kol.collect_cats()))

        # 入库(去重、保留自己记的状态)
        items = [{"creator_id": "u1", "name": "合合暄", "fans": "41.8万", "categories": ["美食"], "history_cats": ["保健食品/膳食营养补充食品", "女装/女士精品"], "coop_tags": ["1", "4"], "gmv": "500w以上", "price": "2000-5000", "live_plan": "11-11 双十一收官"},
                 {"creator_id": "u2", "name": "小野智恩", "fans": 32000, "categories": ["美食", "vlog"], "history_cats": ["女装/女士精品"], "coop_tags": ["4"], "city": "上海"},
                 {"creator_id": "u3", "name": "安吉小朋友", "fans": 235000, "categories": ["护肤"], "history_cats": ["保健食品/膳食营养补充食品"]},
                 {"creator_id": "u4", "name": "四个娃组合", "fans": 12000, "categories": ["母婴"], "history_cats": ["童装/婴儿装/亲子装"]}, {"fans": 9}]
        r1 = kol.upsert(items, "测试")
        rep.check("入库:新增数,没名字没 ID 的行跳过", r1 == {"new": 4, "updated": 0}, str(r1))
        cid1 = db.q_one("SELECT id FROM kol_creators WHERE creator_id='u1'")["id"]
        kol.update(cid1, status="contacted", note="微信已加", by="lisi")
        r2 = kol.upsert([{**items[0], "fans": "42万"}], "测试")
        row = db.q_one("SELECT * FROM kol_creators WHERE id=?", cid1)
        rep.check("再次采集:更新粉丝等采集字段,保留状态/备注/负责人,计数 +1", r2 == {"new": 0, "updated": 1} and row["fans"] == 420000 and row["status"] == "contacted" and row["note"] == "微信已加" and row["owner"] == "lisi" and row["seen_count"] == 2, str(row))
        rep.check("建联:第一次联系的时间自动记下", bool(row["contacted_at"]))
        rs = kol.upsert(sample, "样本")
        rep.check("真实样本整批入库(8 位),入库时不带任何联系方式(联系方式只能经「获取联系方式」任务取)", rs["new"] == 8 and not any(r["phone"] or r["wechat"] for r in db.q_all("SELECT phone, wechat FROM kol_creators WHERE source='样本'")), str(rs))
        db.q_exec("DELETE FROM kol_creators WHERE source='样本'")

        # 接口:筛选 / 排序 / 分页
        lst = user.get("/api/kol").json()
        rep.check("列表:总数、分类/量级/状态统计、目标品类", lst["total"] == 4 and any(c["name"] == "美食" for c in lst["facets"]["categories"]) and any(c["name"].startswith("保健食品") for c in lst["facets"]["history"])
                  and {t["name"]: t["n"] for t in lst["facets"]["tiers"]}["腰部"] == 2 and "保健食品" in lst["targets"], str(lst["facets"]["tiers"]))
        rep.check("列表:默认按综合评分排序", [x["name"] for x in lst["items"]] == ["合合暄", "安吉小朋友", "小野智恩", "四个娃组合"], str([x["name"] for x in lst["items"]]))
        rep.check("筛选:按分类(内容分类和历史合作品类都认)", {x["name"] for x in user.get("/api/kol?category=美食").json()["items"]} == {"合合暄", "小野智恩"}
                  and {x["name"] for x in user.get("/api/kol?category=保健食品/膳食营养补充食品").json()["items"]} == {"合合暄", "安吉小朋友"})
        rep.check("筛选:按量级 / 状态", {x["name"] for x in user.get("/api/kol?tier=腰部").json()["items"]} == {"合合暄", "安吉小朋友"} and [x["name"] for x in user.get("/api/kol?status=contacted").json()["items"]] == ["合合暄"])
        rep.check("筛选:只看高适配 / 高适配加相关内容", {x["name"] for x in user.get("/api/kol?fit=2").json()["items"]} == {"合合暄", "安吉小朋友"} and user.get("/api/kol?fit=1").json()["total"] == 3)
        rep.check("筛选:搜昵称、城市", [x["name"] for x in user.get("/api/kol?q=上海").json()["items"]] == ["小野智恩"] and user.get("/api/kol?q=四个娃").json()["total"] == 1)
        rep.check("排序:按粉丝数;分页", [x["name"] for x in user.get("/api/kol?sort=fans").json()["items"]][0] == "合合暄" and len(user.get("/api/kol?limit=2&offset=2").json()["items"]) == 2)

        # 更新状态和备注
        cid2 = db.q_one("SELECT id FROM kol_creators WHERE creator_id='u2'")["id"]
        rr = user.post(f"/api/kol/{cid2}", json={"status": "shortlist", "note": "报价 3000"})
        rep.check("接口:改状态和备注", rr.status_code == 200 and rr.json()["status"] == "shortlist" and rr.json()["note"] == "报价 3000", rr.text[:120])
        rep.check("接口:状态不对 / 达人不存在拒绝", user.post(f"/api/kol/{cid2}", json={"status": "bogus"}).status_code == 400 and user.post("/api/kol/99999", json={"status": "new"}).status_code == 400)
        csv_ = user.get("/api/kol/export.csv?status=shortlist")
        head = csv_.text.split("\n")[0]
        rep.check("导出 CSV:带 BOM、按当前筛选、含备注", csv_.status_code == 200 and csv_.content.startswith(b"\xef\xbb\xbf") and "小野智恩" in csv_.text and "合合暄" not in csv_.text and "报价 3000" in csv_.text, csv_.text[:120])
        rep.check("导出 CSV:不含手机号微信号列", "手机" not in head and "微信" not in head and "电话" not in head)

        # 目标品类改了 → 重算
        db.save_settings({"kol_targets": "宠物", "kol_content": "宠物"})
        kol.rescore()
        rep.check("目标品类改了:适配度和评分重算", {r["creator_id"]: r["fit"] for r in db.q_all("SELECT creator_id, fit FROM kol_creators")} == {"u1": 0, "u2": 0, "u3": 0, "u4": 0})
        db.save_settings({"kol_targets": TG, "kol_content": CT})
        kol.rescore()
        rep.check("改回来:适配度恢复", {r["creator_id"]: r["fit"] for r in db.q_all("SELECT creator_id, fit FROM kol_creators")} == {"u1": 2, "u2": 1, "u3": 2, "u4": 0})

        # 采集端回传 / 任务下发
        db.q_exec("DELETE FROM crawl_tasks WHERE type='qianfan'")
        db.save_settings({"qf_enabled": "1", "qf_kinds": "", "kol_enabled": "1", "kol_cats": "保健食品,美食", "kol_hours": "72"})
        n = qf.schedule()
        rep.check("任务下发:每个采集品类一个买手广场任务", n == 2 and sorted(t["label"] for t in q("SELECT label FROM crawl_tasks WHERE query='buyers'")) == ["保健食品", "美食"], str(n))
        rep.check("任务下发:有未完成的任务时不重复", qf.schedule() == 0)
        got = col.get("/api/tasks?types=qianfan&worker=w1&shop=s1&shop_name=店1&limit=5").json()["tasks"]
        rep.check("任务下发:声明 qianfan 的采集端领到,不分店铺,带翻页上限", len(got) == 2 and all(t["query"] == "buyers" for t in got) and got[0]["params"]["category"] in ("保健食品", "美食") and got[0]["params"]["pages"] == 5, str(got)[:200])
        t0 = got[0]
        resp = col.post("/api/qianfan/ingest", json={"task_id": t0["id"], "kind": "buyers", "category": t0["params"]["category"],
                                                      "items": [{"creator_id": "u5", "name": "新达人", "fans": 88000, "categories": ["美食"]}, items[0]]})
        rep.check("回传:新增 / 更新数,任务标完成", resp.status_code == 200 and resp.json()["new"] == 1 and resp.json()["updated"] == 1 and q("SELECT status, new_count FROM crawl_tasks WHERE id=?", t0["id"])[0] == {"status": "done", "new_count": 1}, resp.text)
        t1 = got[1]
        col.post("/api/qianfan/ingest", json={"task_id": t1["id"], "kind": "buyers", "error": "登录过期"})
        rep.check("回传:采集端报错 → 任务失败并记原因", q("SELECT status, error FROM crawl_tasks WHERE id=?", t1["id"])[0] == {"status": "failed", "error": "登录过期"})
        rep.check("回传:空数据拒绝", col.post("/api/qianfan/ingest", json={"kind": "buyers", "items": []}).status_code == 400)
        rep.check("下发间隔:刚采完的品类 72 小时内不再下发(失败的一小时内也不重发)", qf.schedule() == 0)
        db.save_settings({"collector_min_cv": "2"})
        old = col.get("/api/tasks?types=qianfan&worker=w1&limit=1")
        new = col.get("/api/tasks?types=qianfan&worker=w1&limit=1&cv=2")
        rep.check("采集端版本门槛:旧版千帆采集端被拒绝(401),新版正常,不影响其它任务类型", old.status_code == 401 and new.status_code == 200 and col.get("/api/tasks?limit=1&worker=g").status_code == 200, f"{old.status_code} {new.status_code}")
        db.save_settings({"collector_min_cv": "0"})
        rep.check("采集端版本门槛:默认关闭,旧采集端照常", col.get("/api/tasks?types=qianfan&worker=w1&limit=1").status_code == 200)
        rep.check("管理员立即采集:不看间隔直接下发,已有未完成的不重复", api_kol.sync_now() == {"created": 2} and api_kol.sync_now() == {"created": 0})

        # 联系方式:默认关;员工点获取 → 任务 → 采集端回传 → 列表只给打码,明文要单独取
        db.q_exec("DELETE FROM crawl_tasks WHERE type='qianfan'")
        c3 = db.q_one("SELECT id, creator_id FROM kol_creators WHERE creator_id='u3'")
        rep.check("联系方式:管理员没开时拒绝", user.post(f"/api/kol/{c3['id']}/contact").status_code == 400 and "contact_enabled" in user.get("/api/kol").json() and user.get("/api/kol").json()["contact_enabled"] is False)
        db.save_settings({"kol_contact_enabled": "1", "kol_contact_cap": "2"})
        r_ = user.post(f"/api/kol/{c3['id']}/contact")
        task_ = db.q_one("SELECT * FROM crawl_tasks WHERE query='buyer_contact'")
        rep.check("联系方式:下发一个读取任务,带达人 ID 和昵称,标为获取中", r_.json() == {"queued": True} and task_ and json.loads(task_["params"])["creator_id"] == "u3" and json.loads(task_["params"])["name"] == "安吉小朋友"
                  and db.q_one("SELECT contact_status FROM kol_creators WHERE id=?", c3["id"])["contact_status"] == "queued", str(task_))
        rep.check("联系方式:同一位不重复下发", user.post(f"/api/kol/{c3['id']}/contact").json()["queued"] is False and db.q_one("SELECT COUNT(*) n FROM crawl_tasks WHERE query='buyer_contact'")["n"] == 1)
        got2 = col.get("/api/tasks?types=qianfan&worker=w2&shop=s1&limit=5").json()["tasks"]
        rep.check("联系方式:采集端领得到任务", [t["query"] for t in got2] == ["buyer_contact"] and got2[0]["params"]["creator_id"] == "u3", str(got2)[:160])
        resp2 = col.post("/api/qianfan/ingest", json={"task_id": got2[0]["id"], "kind": "buyer_contact", "creator_id": "u3", "phone": "139 1234 5678", "wechat": "wx_test_01"})
        row3 = db.q_one("SELECT * FROM kol_creators WHERE id=?", c3["id"])
        rep.check("联系方式:回传后存手机号(去掉空格)和微信号,任务完成", resp2.status_code == 200 and row3["phone"] == "13912345678" and row3["wechat"] == "wx_test_01" and row3["contact_status"] == "ok" and row3["contact_at"]
                  and db.q_one("SELECT status FROM crawl_tasks WHERE id=?", got2[0]["id"])["status"] == "done", resp2.text)
        item3 = next(x for x in user.get("/api/kol").json()["items"] if x["creator_id"] == "u3")
        rep.check("联系方式:列表里不带明文(只有打码手机号和有没有)", item3["has_contact"] and item3["phone_masked"] == "139****5678" and "phone" not in item3 and "wechat" not in item3 and item3["has_wechat"], str({k: item3.get(k) for k in ("has_contact", "phone_masked")}))
        rep.check("联系方式:单独取明文", user.get(f"/api/kol/{c3['id']}/contact").json()["phone"] == "13912345678")
        rep.check("联系方式:已经获取过的不重复下发", user.post(f"/api/kol/{c3['id']}/contact").json()["queued"] is False)
        rep.check("导出:默认不含联系方式;勾选且管理员开启才含", "13912345678" not in user.get("/api/kol/export.csv").text and "13912345678" in user.get("/api/kol/export.csv?contact=1").text and "手机号" in user.get("/api/kol/export.csv?contact=1").text.split("\n")[0])
        db.save_settings({"kol_contact_enabled": "0"})
        rep.check("导出:管理员关了开关,勾选也不含联系方式", "13912345678" not in user.get("/api/kol/export.csv?contact=1").text)
        db.save_settings({"kol_contact_enabled": "1"})
        # 失败 / 对方没开放 / 每日上限 / 批量
        c1 = db.q_one("SELECT id FROM kol_creators WHERE creator_id='u1'")["id"]
        user.post(f"/api/kol/{c1}/contact")
        t_ = col.get("/api/tasks?types=qianfan&worker=w2&shop=s1&limit=5").json()["tasks"][0]
        col.post("/api/qianfan/ingest", json={"task_id": t_["id"], "kind": "buyer_contact", "creator_id": "u1", "error": "搜到 2 位同名达人"})
        rep.check("联系方式:读取失败 → 记原因,任务失败,可以重试", db.q_one("SELECT contact_status, contact_error FROM kol_creators WHERE id=?", c1) == {"contact_status": "error", "contact_error": "搜到 2 位同名达人"}
                  and db.q_one("SELECT status FROM crawl_tasks WHERE id=?", t_["id"])["status"] == "failed")
        rep.check("联系方式:每日上限(2 位)用完后拒绝", user.post(f"/api/kol/{c1}/contact").status_code == 400 and "今天已经获取" in user.post(f"/api/kol/{c1}/contact").json()["detail"])
        db.save_settings({"kol_contact_cap": "10"})
        col.post("/api/qianfan/ingest", json={"kind": "buyer_contact", "creator_id": "u2", "phone": "", "wechat": ""})
        rep.check("联系方式:对方没开放 → 标未开放", db.q_one("SELECT contact_status FROM kol_creators WHERE creator_id='u2'")["contact_status"] == "none")
        db.q_exec("UPDATE kol_creators SET status='shortlist' WHERE creator_id IN ('u1','u4')")
        bt = user.post("/api/kol/contact/batch", json={"status": "shortlist"}).json()
        rep.check("联系方式:批量只给「已入围」且还没取到的下发", bt["queued"] == 2 and bt["candidates"] == 2, str(bt))
        t_new = db.q_one("SELECT id FROM crawl_tasks WHERE query='buyer_contact' AND status='pending' ORDER BY id LIMIT 1")
        col.post("/api/qianfan/ingest", json={"task_id": t_new["id"], "kind": "buyer_contact", "creator_id": "u1", "error": "风控:点眼睛后页面出现「操作过于频繁」"})
        st_ = db.get_settings()
        rep.check("联系方式:遇到风控自动暂停(开关关闭、排队任务作废、记原因)", st_["kol_contact_enabled"] == "0" and "操作过于频繁" in st_["kol_contact_paused"]
                  and db.q_one("SELECT COUNT(*) n FROM crawl_tasks WHERE query='buyer_contact' AND status IN ('pending','running')")["n"] == 0
                  and user.post(f"/api/kol/{c1}/contact").status_code == 400 and user.get("/api/kol").json()["contact_paused"], str(st_["kol_contact_paused"]))
        db.save_settings({"kol_contact_enabled": "1", "kol_contact_paused": ""})
        rep.check("联系方式:缺 creator_id 的回传拒绝", col.post("/api/qianfan/ingest", json={"kind": "buyer_contact", "phone": "1"}).status_code == 400)
    finally:
        db.save_settings(keep)
        db.q_exec("DELETE FROM crawl_tasks WHERE type='qianfan' AND query='buyers'")
        db.q_exec("DELETE FROM kol_creators")
