"""数据归属:员工 A / B 各看各的,历史数据(owner_id 为空)只有管理员看得到;按 id 访问别人的东西一律 404;
图片、导出包、Live 图、复盘、批量操作都按人隔离;达人库全员可见、认领跟进;员工自己改密码。
最后检查所有带路径参数的员工端接口都登记过归属规则(新接口漏登记就失败)。
放在 iso_staff 之前跑,结束时删掉自己建的员工账号和数据,让前端回到免登录。"""
import json
import re

from fastapi.testclient import TestClient


def run(rep, env):
    from app import api_admin as aa, api_kol, auth, db, scope

    app, q = env.app, env.q
    if not any(getattr(r, "path", "").startswith("/api/kol") for r in app.routes):
        app.include_router(api_kol.router)
    print("\n[数据归属]")
    made = {"projects": [], "drafts": [], "playbooks": [], "crawl_tasks": [], "live_jobs": [], "kol_creators": []}
    sa = aa.staff_add(aa.StaffIn(username="own_a", name="小A", password="passA1"))["id"]
    sb = aa.staff_add(aa.StaffIn(username="own_b", name="小B", password="passB1"))["id"]
    auth._fails.clear()
    try:
        ca, cb = TestClient(app), TestClient(app)
        rep.check("两个员工都能登录", ca.post("/api/login", json={"username": "own_a", "password": "passA1"}).status_code == 200
                  and cb.post("/api/login", json={"username": "own_b", "password": "passB1"}).status_code == 200)
        adm = TestClient(app, cookies={"adm": auth.make_token("admin")})

        # 造数据:A 的任务(走接口,验证自动记归属)+ 稿子 / 拆解卡 / 采集任务 / 一键生成;一个历史任务(owner_id 为空)
        pa = ca.post("/api/projects", json={"track": "美食", "keywords": ["早餐归属测试"], "direction": ""}).json()["id"]
        made["projects"].append(pa)
        rep.check("员工建任务:自动记归属(owner_id = 员工 id)", q("SELECT owner_id FROM projects WHERE id=?", pa)[0]["owner_id"] == sa)
        made["crawl_tasks"] += [r["id"] for r in q("SELECT id FROM crawl_tasks WHERE project_id=?", pa)]
        db.q_exec("UPDATE crawl_tasks SET status='running' WHERE project_id=?", pa)   # 不留 pending,免得被当成真实任务
        legacy = db.q_exec("INSERT INTO projects(track,keywords,created_by) VALUES('美食','[\"历史\"]','admin')").lastrowid
        made["projects"].append(legacy)
        for pid in (pa, legacy):
            db.q_exec("INSERT INTO autoruns(project_id,target,status) VALUES(?,3,'done')", pid)
        da = db.q_exec("INSERT INTO drafts(project_id,title,content,status,images) VALUES(?,?,?,?,?)", pa, "A 的稿子", "正文", "ready", json.dumps(["d0_1_x.png"])).lastrowid
        dl = db.q_exec("INSERT INTO drafts(project_id,title,content,status) VALUES(?,?,?,?)", legacy, "历史稿子", "正文", "ready").lastrowid
        made["drafts"] += [da, dl]
        ba = db.q_exec("INSERT INTO playbooks(project_id,name,status) VALUES(?,?,'done')", pa, "A 的拆解").lastrowid
        made["playbooks"].append(ba)
        ta = made["crawl_tasks"][0]
        ja = db.q_exec("INSERT INTO live_jobs(draft_id,image,owner_id,status,title) VALUES(?,?,?,'done','A 的 Live')", da, "lvup_abc.png", sa).lastrowid
        made["live_jobs"].append(ja)

        # 列表
        ids = lambda c, url: {x["id"] for x in c.get(url).json()}
        rep.check("任务列表:A 只看到自己的,看不到历史任务", ids(ca, "/api/projects") == {pa})
        rep.check("任务列表:B 什么都看不到", ids(cb, "/api/projects") == set())
        rep.check("管理员:看得到全部(含历史任务)", {pa, legacy} <= ids(adm, "/api/projects"))
        rep.check("一键任务列表 / 数量按人", ids(ca, "/api/runs") == {pa} and cb.get("/api/runs").json() == [] and cb.get("/api/runs/count").json()["total"] == 0
                  and ca.get("/api/runs/count").json()["total"] == 1)
        lib = lambda c: {x["id"] for x in c.get("/api/library?brief=1").json()}
        rep.check("成品库按人:A 只有自己的稿子,B 为空,管理员全部", lib(ca) == {da} and lib(cb) == set() and {da, dl} <= lib(adm))
        rep.check("成品库带 pid:别人的任务 404", cb.get(f"/api/library?pid={pa}").status_code == 404)

        # 按 id 访问别人的东西:一律 404,且数据没被改
        probes = [("GET", f"/api/runs/{pa}"), ("GET", f"/api/projects/{pa}/drafts"), ("GET", f"/api/projects/{pa}/notes"), ("GET", f"/api/projects/{pa}/auto"),
                  ("PUT", f"/api/projects/{pa}"), ("DELETE", f"/api/runs/{pa}"), ("DELETE", f"/api/projects/{pa}"),
                  ("GET", f"/api/library/{da}"), ("PUT", f"/api/drafts/{da}"), ("DELETE", f"/api/drafts/{da}"), ("POST", f"/api/drafts/{da}/published"),
                  ("GET", f"/api/drafts/{da}/images.zip"), ("POST", f"/api/drafts/{da}/spawn"),
                  ("PUT", f"/api/playbooks/{ba}"), ("DELETE", f"/api/playbooks/{ba}"), ("POST", f"/api/crawl_tasks/{ta}/retry"),
                  ("GET", f"/api/live/jobs/{ja}/download"), ("DELETE", f"/api/live/jobs/{ja}")]
        body = {"PUT": {"title": "x", "content": "x", "tags": [], "image_prompts": [], "keywords": ["x"], "name": "x", "data": {}}, "POST": {"on": True}}
        bad = [f"{m} {u} → {r.status_code}" for m, u in probes
               for r in [cb.request(m, u, json=body.get(m))] if not (r.status_code == 404 and r.json().get("detail") == "不存在")]
        rep.check(f"B 按 id 访问 A 的数据:{len(probes)} 个接口全部 404(不说无权限,不泄露存在)", not bad, "; ".join(bad))
        rep.check("B 的越权请求没有改动数据", q("SELECT title FROM drafts WHERE id=?", da)[0]["title"] == "A 的稿子" and q("SELECT COUNT(*) n FROM projects WHERE id=?", pa)[0]["n"] == 1
                  and q("SELECT COUNT(*) n FROM live_jobs WHERE id=?", ja)[0]["n"] == 1 and q("SELECT published FROM drafts WHERE id=?", da)[0]["published"] == 0)
        rep.check("员工访问历史任务也是 404", ca.get(f"/api/runs/{legacy}").status_code == 404 and ca.get(f"/api/library/{dl}").status_code == 404)
        rep.check("A 访问自己的正常", ca.get(f"/api/runs/{pa}").status_code == 200 and ca.get(f"/api/library/{da}").status_code == 200)
        rep.check("管理员访问任何人的正常", adm.get(f"/api/runs/{pa}").status_code == 200 and adm.get(f"/api/library/{dl}").status_code == 200)
        rep.check("id 不是数字:同样 404", cb.get("/api/runs/abc").status_code in (404, 422))

        # 批量接口:别人的 id 被滤掉
        rep.check("批量登记发布:别人的稿子不算", cb.post("/api/drafts/publish", json={"ids": [da]}).json()["n"] == 0 and q("SELECT published FROM drafts WHERE id=?", da)[0]["published"] == 0)
        rep.check("导出:只有别人的稿子 → 没东西可导", cb.post("/api/export/start", json={"ids": [da]}).status_code == 400)
        rep.check("Live 下单:用别人的稿子图 → 404", cb.post("/api/live/jobs", json={"items": [{"draft_id": da, "image": "d0_1_x.png"}]}).status_code == 404)
        rep.check("Live 列表按人", [j["id"] for j in ca.get("/api/live/jobs").json()] == [ja] and cb.get("/api/live/jobs").json() == [])
        rep.check("Live 清理已完成:只清自己的", cb.post("/api/live/jobs/clear_done").json()["deleted"] == 0 and q("SELECT COUNT(*) n FROM live_jobs WHERE id=?", ja)[0]["n"] == 1)
        rep.check("复盘只算自己的稿子", all(r["id"] != da for r in cb.get("/api/loop/page").json()["rows"]["items"])
                  and any(r["id"] == da for r in ca.get("/api/loop/page?reg=todo").json()["reg"]["items"]))
        rep.check("对号候选:别人的稿子 404", cb.get(f"/api/loop/notes?draft_id={da}").status_code == 404)

        # 文件:生成图按文件名里的稿子 id,Live 上传图按自己的 Live 任务,导出包按登记
        detail = lambda r: r.json().get("detail")
        rep.check("生成图 d{稿子}_…:别人的 → 拦截;自己的 → 放行(文件不存在才 404)", detail(cb.get(f"/images/d{da}_1_x.png")) == "不存在" and detail(ca.get(f"/images/d{da}_1_x.png")) != "不存在")
        rep.check("Live 上传图:只有用到它的人能取", detail(cb.get("/images/lvup_abc.png")) == "不存在" and detail(ca.get("/images/lvup_abc.png")) != "不存在")
        rep.check("其他图片名:员工一律拦截", detail(ca.get("/images/random.png")) == "不存在")
        db.q_exec("INSERT INTO exports(zip, owner_id) VALUES('批次_test_owner.zip', ?)", sa)
        rep.check("导出包:只有导出的人能下载,猜名字也不行", detail(cb.get("/download/批次_test_owner.zip")) == "不存在" and detail(ca.get("/download/批次_test_owner.zip")) != "不存在"
                  and detail(ca.get("/download/批次_没登记.zip")) == "不存在")
        db.q_exec("DELETE FROM exports WHERE zip='批次_test_owner.zip'")
        rep.check("产品和笔记库全员共享", cb.get("/api/products").status_code == 200 and cb.get("/api/notebase").status_code == 200)

        # 达人库:全员可见,认领跟进
        k = db.q_exec("INSERT INTO kol_creators(creator_id,name) VALUES('own_k1','归属测试达人')").lastrowid
        made["kol_creators"].append(k)
        rep.check("达人库全员可见", any(x["id"] == k for x in cb.get("/api/kol").json()["items"]))
        r1 = ca.post(f"/api/kol/{k}/claim", json={"on": True})
        rep.check("认领:负责人记成员工显示名", r1.status_code == 200 and q("SELECT owner FROM kol_creators WHERE id=?", k)[0]["owner"] == "小A", r1.text)
        rep.check("别人已认领:不能抢、不能替人放手", cb.post(f"/api/kol/{k}/claim", json={"on": True}).status_code == 409 and cb.post(f"/api/kol/{k}/claim", json={"on": False}).status_code == 409)
        cb.post(f"/api/kol/{k}", json={"owner": "小B"})
        rep.check("员工不能通过改备注接口改负责人", q("SELECT owner FROM kol_creators WHERE id=?", k)[0]["owner"] == "小A")
        rep.check("只看我跟进的", [x["id"] for x in ca.get("/api/kol?mine=1").json()["items"]] == [k] and cb.get("/api/kol?mine=1").json()["total"] == 0
                  and ca.get("/api/kol").json()["mine_n"] == 1)
        rep.check("放手后别人可以认领", ca.post(f"/api/kol/{k}/claim", json={"on": False}).status_code == 200 and cb.post(f"/api/kol/{k}/claim", json={"on": True}).status_code == 200)

        # 员工改自己的密码
        other = TestClient(app)
        other.post("/api/login", json={"username": "own_a", "password": "passA1"})
        rep.check("改密码:原密码错拒绝", ca.post("/api/me/password", json={"old": "wrong", "new": "newA12"}).status_code == 400)
        auth._fails.clear()
        rep.check("改密码:新密码太短拒绝", ca.post("/api/me/password", json={"old": "passA1", "new": "12"}).status_code == 400)
        ok = ca.post("/api/me/password", json={"old": "passA1", "new": "newA12"})
        rep.check("改密码成功:当前设备保持登录,其他设备下线", ok.status_code == 200 and ca.get("/api/projects").status_code == 200 and other.get("/api/projects").status_code == 401, ok.text)
        rep.check("改密码后:新密码能登录,旧密码不行", TestClient(app).post("/api/login", json={"username": "own_a", "password": "newA12"}).status_code == 200
                  and TestClient(app).post("/api/login", json={"username": "own_a", "password": "passA1"}).status_code == 400)
        rep.check("管理员 / 未登录员工不能用这个接口改密码", adm.post("/api/me/password", json={"old": "x", "new": "yyyyyy"}).status_code == 400)

        # 删除员工:数据不删,员工端没人看得到,管理员仍能看到
        aa.staff_delete(sa)
        rep.check("删除员工:他的任务保留,管理员可见,同名新账号不会继承", pa in ids(adm, "/api/projects") and q("SELECT owner_id FROM projects WHERE id=?", pa)[0]["owner_id"] == sa)
        sa2 = aa.staff_add(aa.StaffIn(username="own_a", name="新小A", password="passA9"))["id"]
        c2 = TestClient(app)
        c2.post("/api/login", json={"username": "own_a", "password": "passA9"})
        rep.check("同名新账号看不到旧账号的数据", sa2 != sa and ids(c2, "/api/projects") == set())
        aa.staff_delete(sa2)

        # 接口登记:每个员工端接口的路径参数都要有归属规则
        from app import api_live, api_qianfan, api_user
        routes = [r for rt in (api_user.router, api_live.router, api_kol.router, api_qianfan.user) for r in rt.routes]
        miss = sorted({f"{r.path} {{{p}}}" for r in routes for p in re.findall(r"{(\w+)(?::\w+)?}", r.path)
                       if p not in scope.PARAM_KIND and (r.path, p) not in scope.SHARED_PARAMS})
        rep.check(f"所有员工端接口({len(routes)} 个)的路径参数都登记了归属规则", not miss, "未登记:" + ", ".join(miss))
        wrong = sorted(f"{path} {{{p}}}" for path, p in scope.SHARED_PARAMS if not any(r.path == path for r in routes))
        rep.check("共享登记里没有已经不存在的接口", not wrong, ", ".join(wrong))
    finally:
        for t, xs in made.items():
            for i in xs:
                db.q_exec(f"DELETE FROM {t} WHERE id=?", i)
        for pid in made["projects"]:
            db.q_exec("DELETE FROM autoruns WHERE project_id=?", pid)
            db.q_exec("DELETE FROM crawl_tasks WHERE project_id=?", pid)
        db.q_exec("DELETE FROM staff WHERE username IN ('own_a','own_b')")
        auth._fails.clear()
