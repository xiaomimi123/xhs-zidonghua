"""Live 图任务队列:选图下单 → Mac 端领取 → 回传成品 → 下载 / 预览 → 失败重试 / 超时退回 / 清理。
成品目录和图片目录都换成临时目录,不碰真实的 data/。"""
import io
import json
import time
import tempfile
import zipfile
from pathlib import Path


def _pvt_zip(with_mov=True, with_jpg=True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if with_jpg:
            z.writestr("IMG_x.pvt/IMG_x.JPG", b"\xff\xd8jpg")
        if with_mov:
            z.writestr("IMG_x.pvt/IMG_x.MOV", b"movdata")
        z.writestr("IMG_x.pvt/metadata.plist", "<plist/>")
    return buf.getvalue()


def _video_ok(jid):
    from app import api_live
    return (api_live.LIVE_DIR / f"{jid}.mp4").is_file()


def run(rep, env):
    from app import api_live, db, imagegen
    user, col, q = env.user, env.col, env.q

    print("\n[Live 图]")
    old_live, old_img = api_live.LIVE_DIR, imagegen.IMG_DIR
    tmp = Path(tempfile.mkdtemp())
    api_live.LIVE_DIR, imagegen.IMG_DIR = tmp / "live", tmp / "images"
    imagegen.IMG_DIR.mkdir()
    try:
        (imagegen.IMG_DIR / "lv_a.png").write_bytes(b"png-a")
        (imagegen.IMG_DIR / "lv_b.png").write_bytes(b"png-b")
        pid = db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('美食','[\"live\"]','')").lastrowid
        did = db.q_exec("INSERT INTO drafts(project_id,title,content,images,status) VALUES(?,?,?,?,'ready')", pid, "Live测试稿", "正文", '["lv_a.png","lv_b.png"]').lastrowid
        other = db.q_exec("INSERT INTO drafts(project_id,title,content,images,status) VALUES(?,?,?,?,'ready')", pid, "别的稿", "正文", '["other.png"]').lastrowid

        r = user.post("/api/live/jobs", json={"items": [{"draft_id": did, "image": "lv_a.png"}]})
        rep.check("下单:成功建 1 个任务", r.status_code == 200 and r.json()["created"] == 1, r.text)
        r2 = user.post("/api/live/jobs", json={"items": [{"draft_id": did, "image": "lv_a.png"}]})
        rep.check("下单:同图同参数不重复建", r2.json()["created"] == 0 and r2.json()["skipped"] == 1 and r2.json()["ids"] == r.json()["ids"], r2.text)
        r3 = user.post("/api/live/jobs", json={"items": [{"draft_id": did, "image": "lv_a.png"}], "opts": {"zoom": 1.15}})
        rep.check("下单:参数不同算新任务", r3.json()["created"] == 1, r3.text)
        rep.check("下单:没选图 / 图不属于这篇稿子 / 稿子不存在 都拒绝",
                  user.post("/api/live/jobs", json={"items": []}).status_code == 400
                  and user.post("/api/live/jobs", json={"items": [{"draft_id": did, "image": "other.png"}]}).status_code == 400
                  and user.post("/api/live/jobs", json={"items": [{"draft_id": did, "image": "../../secret.key"}]}).status_code == 400
                  and user.post("/api/live/jobs", json={"items": [{"draft_id": 99999, "image": "lv_a.png"}]}).status_code == 404)
        o = api_live.clean_opts({"zoom": 9, "seconds": "x", "focus_y": -3, "fit": "evil"})
        rep.check("参数:超出范围的值被收回合法范围", o["zoom"] == 1.3 and o["seconds"] == 5.0 and o["focus_y"] == 0 and o["fit"] == "crop", str(o))
        jid = r.json()["ids"][0]

        # 任务列表:状态在服务端筛选 + 分页 + 汇总(排队的任务很多时,已完成的不能被挤出列表)
        extra = []
        for n_ in range(5):   # 再造 5 个排队中的、让「最新的几个」全是排队中
            extra += user.post("/api/live/jobs", json={"items": [{"draft_id": did, "image": "lv_b.png"}], "opts": {"zoom": 1.05 + n_ * 0.02}}).json()["ids"]
        old_done = db.q_exec("INSERT INTO live_jobs(draft_id,image,opts,status,stage,mode,created_by) VALUES(?,?,?,'done','live','zoom','')", did, "lv_a.png", '{"zoom": 1.01}').lastrowid
        extra.append(old_done)
        sm = user.get("/api/live/jobs/summary").json()
        newest = user.get("/api/live/jobs?limit=3").json()
        done_only = user.get("/api/live/jobs?status=done&limit=3").json()
        run_only = user.get("/api/live/jobs?status=run").json()
        rep.check("任务列表:汇总各状态的数量", sm["all"] == sm["run"] + sm["done"] + sm["err"] and sm["done"] >= 1 and sm["run"] >= 6, str(sm))
        rep.check("任务列表:status=done 只返回已完成的(不被最新的排队任务挤掉)", done_only and all(x["status"] == "done" for x in done_only) and len(done_only) == min(3, sm["done"]) and any(x["id"] == old_done for x in user.get("/api/live/jobs?status=done").json()))
        rep.check("任务列表:status=run 只返回排队中 / 进行中的", run_only and all(x["status"] in ("pending", "running") for x in run_only) and len(run_only) == sm["run"])
        rep.check("任务列表:offset 翻页,汇总里有发起人", [x["id"] for x in user.get("/api/live/jobs?limit=2&offset=1").json()] == [x["id"] for x in user.get("/api/live/jobs?limit=3").json()[1:3]]
                  and isinstance(sm["who"], dict) and sum(sm["who"].values()) == sm["all"] and newest)
        db.q_exec(f"DELETE FROM live_jobs WHERE id IN ({','.join(str(i) for i in extra)})")   # 清掉自己造的,不影响后面的检查

        # Mac 端
        from fastapi.testclient import TestClient
        anon = TestClient(env.app)
        rep.check("Mac 端接口:没有 Token 拒绝", anon.get("/api/live/worker/claim").status_code == 401)
        st = user.get("/api/live/status").json()
        rep.check("状态:还没有 Mac 连接时显示离线", not st["online"] and st["pending"] == 2, str(st))
        c = col.get("/api/live/worker/claim?worker=mac-test").json()["job"]
        rep.check("领任务:先进先出,返回参数和原图地址", c and c["id"] == jid and c["opts"]["zoom"] == 1.08 and c["source"].endswith(f"/{jid}/source"), str(c))
        rep.check("领任务:带上发起人(Mac 端按它分文件夹),没有记录的归管理员", c.get("by") == (db.q_one("SELECT created_by FROM live_jobs WHERE id=?", jid)["created_by"] or "管理员"), str(c))
        ow = col.get("/api/live/worker/owners")
        rep.check("发起人对照表:需要 Token,按任务号给出发起人", anon.get("/api/live/worker/owners").status_code == 401 and ow.status_code == 200 and ow.json()[str(jid)] == c["by"], str(ow.text[:120]))
        import sys
        from pathlib import Path as _P
        sys.path.insert(0, str(_P(__file__).resolve().parents[2] / "tools" / "livephoto"))
        if (_P(__file__).resolve().parents[2] / "tools" / "livephoto" / "sort_by_owner.py").is_file():   # Mac 端脚本不打进镜像(.dockerignore),容器里跑测试时没有,只在项目目录里直接跑才检查
            import sort_by_owner as sbo
            rep.check("分文件夹的名字:去掉路径符号,空的归管理员", sbo.owner_dir("丽/敏:") == "丽敏" and sbo.owner_dir("") == "管理员" and sbo.owner_dir(None) == "管理员")
            with tempfile.TemporaryDirectory() as td:
                root = _P(td)
                (root / "IMG_L1_LIVE.pvt").mkdir(); (root / "IMG_L2_LIVE.pvt").mkdir(); (root / "IMG_L9_LIVE.pvt").mkdir(); (root / "别的文件.txt").write_text("x")
                (root / "丽敏").mkdir(); (root / "丽敏" / "IMG_L3_LIVE.pvt").mkdir(); (root / "冰玲").mkdir(); (root / "冰玲" / "IMG_L2_LIVE.pvt").mkdir()
                mv, okn = sbo.plan(root, {"1": "丽敏", "2": "冰玲", "3": "丽敏"})
                names = {(a.parent.name, b.parent.name, a.name) for a, b in mv}
                rep.check("整理旧文件:按任务号归到发起人文件夹,已在正确位置的不动,查不到的归「未知」,别的文件不碰",
                          okn == 2 and names == {(root.name, "丽敏", "IMG_L1_LIVE.pvt"), (root.name, "冰玲", "IMG_L2_LIVE.pvt"), (root.name, "未知", "IMG_L9_LIVE.pvt")}, str((mv, okn)))
        rep.check("领任务后 Mac 在线", user.get("/api/live/status").json()["online"])
        rep.check("下载原图", col.get(c["source"]).content == b"png-a")
        rep.check("回传:不是 zip / 缺视频 / 缺图片 都拒绝",
                  col.put(f"/api/live/worker/jobs/{jid}/result", content=b"junk").status_code == 400
                  and col.put(f"/api/live/worker/jobs/{jid}/result", content=_pvt_zip(with_mov=False)).status_code == 400
                  and col.put(f"/api/live/worker/jobs/{jid}/result", content=_pvt_zip(with_jpg=False)).status_code == 400)
        ok = col.put(f"/api/live/worker/jobs/{jid}/result?worker=mac-test", content=_pvt_zip())
        rep.check("回传:成功后任务完成", ok.status_code == 200 and db.q_one("SELECT status, result_size FROM live_jobs WHERE id=?", jid)["status"] == "done")
        rep.check("回传:重复回传被拒绝(任务已不在生成中)", col.put(f"/api/live/worker/jobs/{jid}/result", content=_pvt_zip()).status_code == 409)
        dl = user.get(f"/api/live/jobs/{jid}/download")
        rep.check("下载成品:是 zip,里面有 JPG、MOV、metadata.plist", dl.status_code == 200 and {n.split("/")[-1] for n in zipfile.ZipFile(io.BytesIO(dl.content)).namelist()} == {"IMG_x.JPG", "IMG_x.MOV", "metadata.plist"})
        rep.check("预览:取出 MOV", user.get(f"/api/live/jobs/{jid}/preview").content == b"movdata")
        row = next(x for x in user.get("/api/live/jobs").json() if x["id"] == jid)
        rep.check("列表:带稿子标题、成品标记、Mac 名称", row["title"] == "Live测试稿" and row["has_result"] and row["worker"] == "mac-test", str(row))

        # 失败 / 重试
        c2 = col.get("/api/live/worker/claim?worker=mac-test").json()["job"]
        col.post(f"/api/live/worker/jobs/{c2['id']}/fail", json={"error": "ffmpeg 不存在"})
        e = db.q_one("SELECT status, error FROM live_jobs WHERE id=?", c2["id"])
        rep.check("失败:记录原因", e["status"] == "error" and "ffmpeg" in e["error"], str(e))
        rep.check("没有待领任务时返回空", col.get("/api/live/worker/claim").json()["job"] is None)
        user.post(f"/api/live/jobs/{c2['id']}/retry")
        rep.check("重试:回到待领取并清掉错误", db.q_one("SELECT status, error FROM live_jobs WHERE id=?", c2["id"]) == {"status": "pending", "error": ""})

        # 在线判断:机器忙着做任务时不发心跳,不能因此显示离线
        db.q_exec("INSERT INTO live_workers(worker,ip,last_seen,done) VALUES('busy-mac','',?,0) ON CONFLICT(worker) DO UPDATE SET last_seen=excluded.last_seen", time.time() - 300)
        db.q_exec("DELETE FROM live_workers WHERE worker!='busy-mac'")
        rep.check("在线:心跳超过 60 秒、手上也没有任务,显示离线", not user.get("/api/live/status").json()["online"])
        bj = db.q_exec("INSERT INTO live_jobs(image,opts,created_by,mode,stage,status,worker,claimed_at) VALUES('x.png','{}','x','zoom','live','running','busy-mac',datetime('now','-90 seconds'))").lastrowid
        st2 = user.get("/api/live/status").json()
        rep.check("在线:机器正在做一条任务(没有心跳)也算在线,并标出忙碌", st2["online"] and st2["workers"][0]["busy"] is True, str(st2))
        db.q_exec("UPDATE live_jobs SET claimed_at=datetime('now','-15 minutes') WHERE id=?", bj)
        rep.check("在线:任务领走超过 10 分钟还没动静,不再算在线(可能死机了)", not user.get("/api/live/status").json()["online"])
        db.q_exec("DELETE FROM live_jobs WHERE id=?", bj)

        # 超时退回
        col.get("/api/live/worker/claim?worker=sleepy")
        db.q_exec("UPDATE live_jobs SET claimed_at=datetime('now','-30 minutes') WHERE id=?", c2["id"])
        user.get("/api/live/jobs")
        rep.check("超时:领走很久没回传的任务自动退回", db.q_one("SELECT status, worker FROM live_jobs WHERE id=?", c2["id"]) == {"status": "pending", "worker": ""})
        rep.check("生成中的任务不能重新生成", (col.get("/api/live/worker/claim").json()["job"] is not None) and user.post(f"/api/live/jobs/{c2['id']}/retry").status_code == 400)

        # 清理
        rep.check("清理已完成:删记录也删成品文件", user.post("/api/live/jobs/clear_done").json()["deleted"] == 1 and not (api_live.LIVE_DIR / f"{jid}.zip").exists())
        rep.check("删除任务", user.delete(f"/api/live/jobs/{c2['id']}").status_code == 200 and user.get(f"/api/live/jobs/{c2['id']}/download").status_code == 404)

        # ---------- 真实动作模式:先视频 worker 生成视频,再轮到 Mac ----------
        print()
        print("[Live 图 · 真实动作]")
        mo = user.get("/api/live/motions").json()
        rep.check("动作模板:至少有手持轻晃 / 手转产品 / 镜头靠近", {"sway", "rotate", "push"} <= {m["key"] for m in mo}, str(mo))
        o = api_live.clean_opts({"mode": "motion", "motion": "nope", "seconds": 5, "zoom": 1.2})
        rep.check("参数:真实动作模式的模板 / 时长被收回合法值", o["mode"] == "motion" and o["motion"] == "sway" and o["seconds"] == 3.0, str(o))
        rep.check("参数:模式不认识时按慢推处理", api_live.clean_opts({"mode": "evil"})["mode"] == "zoom")
        rm = user.post("/api/live/jobs", json={"items": [{"draft_id": did, "image": "lv_b.png"}], "opts": {"mode": "motion", "motion": "rotate"}})
        mid = rm.json()["ids"][0]
        rep.check("真实动作下单:先进视频阶段", db.q_one("SELECT mode, stage, status FROM live_jobs WHERE id=?", mid) == {"mode": "motion", "stage": "video", "status": "pending"}, rm.text)
        rep.check("真实动作下单:同图同模板不重复", user.post("/api/live/jobs", json={"items": [{"draft_id": did, "image": "lv_b.png"}], "opts": {"mode": "motion", "motion": "rotate"}}).json()["skipped"] == 1)
        rep.check("Mac 领不到还在等视频的任务", col.get("/api/live/worker/claim?worker=mac-test").json()["job"] is None)
        rep.check("视频 worker 接口:没有 Token 拒绝", anon.get("/api/live/vworker/claim").status_code == 401)
        st = user.get("/api/live/status").json()
        rep.check("状态:待生成视频的任务单独计数,视频 worker 还没来 = 离线", st["video"]["pending"] == 1 and not st["video"]["online"], str(st))
        vj = col.get("/api/live/vworker/claim?worker=gpu-pc").json()["job"]
        rep.check("视频 worker 领任务:带动作提示词、时长和原图地址", vj and vj["id"] == mid and "rotates the product" in vj["prompt"] and vj["seconds"] == 3.0 and vj["source"].endswith("/source"), str(vj))
        rep.check("视频 worker 下载原图", col.get(vj["source"]).content == b"png-b")
        rep.check("视频 worker 在线", user.get("/api/live/status").json()["video"]["online"])
        rep.check("回传视频:不是 mp4 / 空 都拒绝", col.put(f"/api/live/vworker/jobs/{mid}/video", content=b"junk-junk-junk").status_code == 400
                  and col.put(f"/api/live/vworker/jobs/{mid}/video", content=b"").status_code == 400)
        fake_mp4 = bytes([0, 0, 0, 24]) + b"ftypmp42" + bytes(40)
        rep.check("回传视频:成功后进入 Mac 队列", col.put(f"/api/live/vworker/jobs/{mid}/video?worker=gpu-pc", content=fake_mp4).status_code == 200
                  and db.q_one("SELECT status, stage FROM live_jobs WHERE id=?", mid) == {"status": "pending", "stage": "live"})
        rep.check("回传视频:重复回传被拒绝", col.put(f"/api/live/vworker/jobs/{mid}/video", content=fake_mp4).status_code == 409)
        rep.check("用户端能先看视频", user.get(f"/api/live/jobs/{mid}/video").content == fake_mp4)
        mj = col.get("/api/live/worker/claim?worker=mac-test").json()["job"]
        rep.check("视频好了 Mac 才领得到:带视频地址和模式", mj and mj["id"] == mid and mj["mode"] == "motion" and mj["video"].endswith("/video"), str(mj))
        rep.check("Mac 下载视频", col.get(mj["video"]).content == fake_mp4)
        col.put(f"/api/live/worker/jobs/{mid}/result?worker=mac-test", content=_pvt_zip())
        rows = {x["id"]: x for x in user.get("/api/live/jobs").json()}
        rep.check("列表:真实动作任务带模板名称、有视频、有成品", rows[mid]["motion"] == "手转产品" and rows[mid]["has_video"] and rows[mid]["has_result"] and rows[mid]["mode"] == "motion", str(rows[mid]))

        # 失败与重试:视频失败 → 重新生成视频;Mac 失败 → 只重做实况(不重拍视频)
        r2 = user.post("/api/live/jobs", json={"items": [{"draft_id": did, "image": "lv_a.png"}], "opts": {"mode": "motion", "motion": "push"}}).json()["ids"][0]
        v2 = col.get("/api/live/vworker/claim?worker=gpu-pc").json()["job"]
        col.post(f"/api/live/vworker/jobs/{v2['id']}/fail", json={"error": "ComfyUI 没有启动"})
        e = db.q_one("SELECT status, stage, error FROM live_jobs WHERE id=?", r2)
        rep.check("视频失败:记录原因,停在视频阶段", e["status"] == "error" and e["stage"] == "video" and "ComfyUI" in e["error"], str(e))
        rep.check("视频失败重试:没有视频文件 → 回到视频阶段", user.post(f"/api/live/jobs/{r2}/retry").json()["stage"] == "video")
        col.get("/api/live/vworker/claim?worker=gpu-pc")
        col.put(f"/api/live/vworker/jobs/{r2}/video", content=fake_mp4)
        col.get("/api/live/worker/claim?worker=mac-test")
        col.post(f"/api/live/worker/jobs/{r2}/fail", json={"error": "makelive 报错"})
        rep.check("Mac 失败重试:视频还在 → 只重做实况", user.post(f"/api/live/jobs/{r2}/retry").json()["stage"] == "live" and _video_ok(r2))
        rep.check("强制重拍视频:删掉旧视频回到视频阶段", user.post(f"/api/live/jobs/{r2}/retry?video=1").json()["stage"] == "video" and not _video_ok(r2))
        rep.check("删除任务:视频和成品文件一起删", user.delete(f"/api/live/jobs/{mid}").status_code == 200 and not (api_live.LIVE_DIR / f"{mid}.mp4").exists() and not (api_live.LIVE_DIR / f"{mid}.zip").exists())

        # ---------- 上传图片 / 产品图生成场景图 ----------
        print()
        print("[Live 图 · 上传与生成场景图]")
        db.q_exec("DELETE FROM live_jobs WHERE status IN ('pending','running','error')")   # 前面用例留下的排队任务清掉,免得抢在前面
        import base64
        png = bytes([0x89]) + b"PNG" + bytes([0x0D, 0x0A, 0x1A, 0x0A]) + b"fake-png-body"
        url = lambda b, mime="png": f"data:image/{mime};base64," + base64.b64encode(b).decode()
        up = user.post("/api/live/jobs/upload", json={"images": [url(png)], "names": ["我的产品图.png"], "opts": {"mode": "motion", "motion": "sway"}})
        uid = up.json()["ids"][0]
        row = db.q_one("SELECT * FROM live_jobs WHERE id=?", uid)
        rep.check("上传图片:建任务,没有稿子、来源是上传、真实动作先进视频阶段",
                  up.status_code == 200 and row["src"] == "upload" and row["draft_id"] is None and row["stage"] == "video" and row["title"] == "我的产品图" and row["image"].startswith("lvup_"), up.text)
        rep.check("上传图片:图片存进图片目录,可以直接访问", (imagegen.IMG_DIR / row["image"]).is_file())
        rep.check("上传图片:同一张图同参数不重复", user.post("/api/live/jobs/upload", json={"images": [url(png)], "opts": {"mode": "motion", "motion": "sway"}}).json()["skipped"] == 1)
        rep.check("上传图片:不是图片 / SVG / 没有图 都拒绝",
                  user.post("/api/live/jobs/upload", json={"images": [url(b"not-an-image")]}).status_code == 400
                  and user.post("/api/live/jobs/upload", json={"images": [url(b"<svg onload=alert(1)>", "svg+xml")]}).status_code == 400
                  and user.post("/api/live/jobs/upload", json={"images": []}).status_code == 400)
        rep.check("上传图片:视频 worker 能领到并下载这张图", col.get("/api/live/vworker/claim?worker=gpu-pc").json()["job"]["id"] == uid and col.get(f"/api/live/vworker/jobs/{uid}/source").content == (imagegen.IMG_DIR / row["image"]).read_bytes())
        lst = {x["id"]: x for x in user.get("/api/live/jobs").json()}
        rep.check("列表:上传任务显示自己的名称和来源", lst[uid]["title"] == "我的产品图" and lst[uid]["src"] == "upload", str(lst[uid]))
        user.delete(f"/api/live/jobs/{uid}")
        rep.check("删除上传任务:只有它在用的图片一起删除", not (imagegen.IMG_DIR / row["image"]).exists())

        sc = user.get("/api/live/scenes").json()["scenes"]
        rep.check("场景库:返回生活场景,没有棚拍", len(sc) >= 10 and not any("棚拍" in x for x in sc), str(sc[:3]))
        pid2 = db.q_exec("INSERT INTO products(name,images) VALUES('测试产品','[]')").lastrowid
        rep.check("生成场景图:产品没有图 / 什么都没选 / 张数不对 都拒绝",
                  user.post("/api/live/jobs/generate", json={"product_id": pid2}).status_code == 400
                  and user.post("/api/live/jobs/generate", json={}).status_code == 400
                  and user.post("/api/live/jobs/generate", json={"images": [url(png)], "count": 11}).status_code == 400)
        from app import api_live as _al
        rep.check("产品图生成场景:一次最多 10 张", _al.MAX_GEN == 10)
        calls = []
        _gen0 = imagegen.generate_image

        def _fake_gen(prompt, name, s=None, refs=None):
            calls.append((prompt, name, [r.name for r in (refs or [])]))
            (imagegen.IMG_DIR / f"{name}.png").write_bytes(png + name.encode())
            return f"{name}.png"
        imagegen.generate_image = _fake_gen
        try:
            g = user.post("/api/live/jobs/generate", json={"images": [url(png)], "scene": "", "note": "一只手拿着罐子", "count": 2, "opts": {"mode": "motion", "motion": "rotate"}})
            gids = g.json()["ids"]
            rep.check("生成场景图:建 2 个任务,先进场景图阶段", g.status_code == 200 and len(gids) == 2 and {db.q_one("SELECT stage FROM live_jobs WHERE id=?", i)["stage"] for i in gids} == {"image"}, g.text)
            rep.check("生成场景图:视频 worker 和 Mac 都领不到还没有图的任务", col.get("/api/live/vworker/claim?worker=gpu-pc").json()["job"] is None and col.get("/api/live/worker/claim?worker=mac-test").json()["job"] is None)
            rep.check("场景图线程:一次处理一个", api_live.run_gen_once() is True)
            a = db.q_one("SELECT status, stage, image, error FROM live_jobs WHERE id=?", gids[0])
            rep.check("场景图生成成功:图片落盘,进入视频阶段", a["status"] == "pending" and a["stage"] == "video" and a["image"].startswith("lvgen_") and (imagegen.IMG_DIR / a["image"]).is_file(), str(a))
            pr, nm, rf = calls[0]
            rep.check("场景图提示词:保持产品外观 + 手机随手拍风格 + 自动场景 + 带参考图",
                      pr.startswith("以参考图中的产品为主体") and "手机随手拍的生活照风格" in pr and "场景:" in pr and "一只手拿着罐子" in pr and rf and rf[0].startswith("lvref_") and "棚拍广告" not in pr.replace("不是棚拍,不是广告大片", ""), pr[:200])
            api_live.run_gen_once()
            rep.check("自动场景:两张换不同场景", calls[0][0] != calls[1][0] and "场景:" in calls[1][0])
            rep.check("没有待生成的任务时返回 False", api_live.run_gen_once() is False)
            vjob = col.get("/api/live/vworker/claim?worker=gpu-pc").json()["job"]
            rep.check("场景图好了视频 worker 才领得到,源图就是生成的图", vjob and vjob["id"] == gids[0] and col.get(vjob["source"]).content == (imagegen.IMG_DIR / a["image"]).read_bytes(), f"{vjob} {gids} {a}")
            # 失败 → 记录原因 → 重试回到场景图阶段
            def _boom(prompt, name, s=None, refs=None):
                raise RuntimeError("生图接口 401")
            imagegen.generate_image = _boom
            f = user.post("/api/live/jobs/generate", json={"images": [url(png + b"2")], "count": 1}).json()["ids"][0]
            api_live.run_gen_once()
            e = db.q_one("SELECT status, stage, error FROM live_jobs WHERE id=?", f)
            rep.check("场景图失败:记录原因,停在场景图阶段", e["status"] == "error" and e["stage"] == "image" and "401" in e["error"], str(e))
            rep.check("场景图失败重试:回到场景图阶段(不会误进视频阶段)", user.post(f"/api/live/jobs/{f}/retry").json()["stage"] == "image")
        finally:
            imagegen.generate_image = _gen0

        # ---------- 每张拍几条(真实动作多拍,挑最好的) ----------
        print()
        print("[Live 图 · 每张拍几条]")
        mo3 = {"mode": "motion", "motion": "sway", "fit": "crop"}
        t1 = user.post("/api/live/jobs", json={"items": [{"draft_id": did, "image": "lv_a.png"}], "opts": mo3, "takes": 3}).json()
        tk = sorted(r["opts"].get("take") for r in db.q_all("SELECT opts FROM live_jobs WHERE id IN (%s)" % ",".join(str(i) for i in t1["ids"])))
        rep.check("拍 3 条:建 3 个任务,参数里带第几条", t1["created"] == 3 and tk == [1, 2, 3], str(t1))
        t2 = user.post("/api/live/jobs", json={"items": [{"draft_id": did, "image": "lv_a.png"}], "opts": mo3, "takes": 3}).json()
        rep.check("拍 3 条:重复提交不重复建", t2["created"] == 0 and t2["skipped"] == 3, str(t2))
        t3 = user.post("/api/live/jobs", json={"items": [{"draft_id": did, "image": "lv_a.png"}], "opts": mo3, "takes": 4}).json()
        rep.check("加到 4 条:只补第 4 条", t3["created"] == 1 and t3["skipped"] == 3, str(t3))
        t4 = user.post("/api/live/jobs", json={"items": [{"draft_id": did, "image": "lv_b.png"}], "opts": mo3, "takes": 99}).json()
        rep.check("条数上限 4", t4["created"] == 4, str(t4))
        t5 = user.post("/api/live/jobs", json={"items": [{"draft_id": did, "image": "lv_b.png"}], "opts": {"mode": "zoom"}, "takes": 3}).json()
        rep.check("慢推镜头没有随机性,只做 1 条", t5["created"] == 1, str(t5))
        t6 = user.post("/api/live/jobs", json={"items": [{"draft_id": did, "image": "lv_a.png"}], "opts": mo3}).json()
        rep.check("不传条数 = 1 条", t6["created"] == 1, str(t6))
        up2 = user.post("/api/live/jobs/upload", json={"images": [url(png + b"takes")], "names": ["多拍.png"], "opts": mo3, "takes": 2}).json()
        rep.check("上传图片也能多拍", up2["created"] == 2, str(up2))
        lst2 = user.get("/api/live/jobs").json()
        rep.check("列表里带出第几条", any((x["opts"] or {}).get("take") == 2 for x in lst2))

        # ---------- 换背景(主体不变):服务端改背景 → 视频 worker 抠主体贴回 → 再接 Live ----------
        print()
        print("[Live 图 · 换背景]")
        db.q_exec("DELETE FROM live_jobs WHERE status IN ('pending','running','error')")
        rep.check("主体名词:只收英文字母,去重、最多 8 个,全是无效值时用默认", api_live.clean_subject(["Hand", "hand", "手", "jar!", "x"]) == ["hand"] and api_live.clean_subject(["手"]) == api_live.DEFAULT_SUBJECT and len(api_live.clean_subject([f"item{chr(97 + i)}" * 1 for i in range(12)])) <= 8)
        ihdr = bytes([0x89]) + b"PNG" + bytes([0x0D, 0x0A, 0x1A, 0x0A]) + (13).to_bytes(4, "big") + b"IHDR" + (1080).to_bytes(4, "big") + (1920).to_bytes(4, "big") + bytes(5)
        rep.check("按原图比例出图:读 PNG 宽高,1080×1920 = 9:16", api_live._nearest_ratio(api_live._img_size(ihdr)) == "9:16" and api_live._nearest_ratio(None) == "3:4")
        sj = user.get("/api/live/subjects").json()
        rep.check("主体选项:有中文名和默认值", {"hand", "jar", "box"} <= {x["key"] for x in sj["subjects"]} and sj["default"], str(sj)[:120])
        bg = user.post("/api/live/jobs/bgswap", json={"images": [url(ihdr)], "names": ["黄金奶.png"], "scenes": ["白色大理石台面", "木桌窗边", " "], "opts": {"mode": "motion", "motion": "sway"}})
        bids = bg.json()["ids"]
        brow = db.q_one("SELECT * FROM live_jobs WHERE id=?", bids[0])
        rep.check("换背景下单:2 个场景 = 2 个任务,先进「改背景」阶段,不带图", bg.status_code == 200 and len(bids) == 2 and brow["stage"] == "bg" and brow["src"] == "bgswap" and brow["image"] == "" and brow["title"] == "黄金奶·换背景", bg.text)
        g0 = brow["gen"]
        rep.check("换背景任务记录:原图、场景、主体、原图比例", g0["orig"].startswith("lvup_") and g0["scene"] == "白色大理石台面" and g0["background"] == api_live.DEFAULT_BG and g0["subject"] == [] and g0["ratio"] == "9:16", str(g0))
        rep.check("换背景:同图同场景不重复", user.post("/api/live/jobs/bgswap", json={"images": [url(ihdr)], "scenes": ["白色大理石台面"], "opts": {"mode": "motion", "motion": "sway"}}).json()["skipped"] == 1)
        rep.check("换背景:同图同场景但背景选项不同 = 新任务(不再被当成重复)", user.post("/api/live/jobs/bgswap", json={"images": [url(ihdr)], "scenes": ["白色大理石台面"], "background": ["table", "wall", "window"], "opts": {"mode": "motion", "motion": "sway"}}).json()["created"] == 1)
        lg = user.post("/api/live/jobs/bgswap", json={"images": [url(ihdr)], "scenes": ["旧方式"], "subject": ["hand", "jar"], "opts": {"mode": "zoom"}}).json()["ids"][0]
        lgg = db.q_one("SELECT gen FROM live_jobs WHERE id=?", lg)["gen"]
        rep.check("换背景:明确给了主体且没给背景 = 按旧方式(抠主体)", lgg["subject"] == ["hand", "jar"] and lgg["background"] == [], str(lgg))
        bo = user.get("/api/live/bg_options").json()
        rep.check("背景选项:有桌面 / 墙面等,默认桌面 + 墙面", {"table", "wall", "window"} <= {x["key"] for x in bo["items"]} and bo["default"] == ["table", "wall"], str(bo)[:120])
        rep.check("换背景:不选场景 = 自动 1 个;图太多 / 不是图片 都拒绝",
                  user.post("/api/live/jobs/bgswap", json={"images": [url(png + b"auto")], "opts": {"mode": "zoom"}}).json()["created"] == 1
                  and user.post("/api/live/jobs/bgswap", json={"images": [url(png + bytes([i])) for i in range(4)]}).status_code == 400
                  and user.post("/api/live/jobs/bgswap", json={"images": [url(b"junk")]}).status_code == 400)
        rep.check("还在改背景的任务,视频 worker 和 Mac 都领不到", col.get("/api/live/vworker/claim?worker=gpu-pc").json()["job"] is None and col.get("/api/live/worker/claim?worker=mac-test").json()["job"] is None)
        seen = []
        _gen1 = imagegen.generate_image

        def _fake_edit(prompt, name, s=None, refs=None):
            seen.append((prompt, (s or {}).get("image_size"), [r.name for r in (refs or [])]))
            (imagegen.IMG_DIR / f"{name}.png").write_bytes(png + b"edited-bg")
            return f"{name}.png"
        imagegen.generate_image = _fake_edit
        try:
            rep.check("服务端改背景:处理一个", api_live.run_gen_once() is True)
            pr, sz, rf = seen[0]
            rep.check("改背景提示词:保持主体不变 + 新场景 + 随手拍风格;按原图比例出图;原图当参考",
                      pr.startswith("保持图中所有的手、产品") and "白色大理石台面" in pr and "手机随手拍的生活照风格" in pr and sz == "9:16" and rf and rf[0] == g0["orig"], f"{pr[:80]} {sz} {rf}")
            b1 = db.q_one("SELECT status, stage, gen FROM live_jobs WHERE id=?", bids[0])
            rep.check("改背景成功:记下新图,进入「抠主体合成」阶段", b1["status"] == "pending" and b1["stage"] == "mask" and b1["gen"]["edit"].startswith("lvgen_") and (imagegen.IMG_DIR / b1["gen"]["edit"]).is_file(), str(b1))
            api_live.run_gen_once()
        finally:
            imagegen.generate_image = _gen1
        user_vid = user.post("/api/live/jobs/upload", json={"images": [url(png + b"vid-first")], "opts": {"mode": "motion", "motion": "sway"}}).json()["ids"][0]
        mj = col.get("/api/live/vworker/claim?worker=gpu-pc").json()["job"]
        rep.check("视频 worker 优先领「抠主体合成」(快),不先领排在前面的视频任务",
                  mj and mj["stage"] == "mask" and mj["id"] == bids[0] and mj["mode"] == "background" and mj["background"] == api_live.DEFAULT_BG and mj["subject"] == [] and mj["orig"].endswith("/file/orig") and mj["edit"].endswith("/file/edit"), str(mj))
        rep.check("视频 worker 取原图和改过背景的图", col.get(mj["orig"]).content == (imagegen.IMG_DIR / g0["orig"]).read_bytes() and col.get(mj["edit"]).content.endswith(b"edited-bg") and col.get(f"/api/live/vworker/jobs/{mj['id']}/file/nope").status_code == 404)
        rep.check("回传合成图:不是图片 / 空 都拒绝", col.put(f"/api/live/vworker/jobs/{mj['id']}/image", content=b"junk").status_code == 400 and col.put(f"/api/live/vworker/jobs/{mj['id']}/image", content=b"").status_code == 400)
        final = png + b"composited"
        rep.check("回传合成图:成功后真实动作进入视频阶段,任务里有新图", col.put(f"/api/live/vworker/jobs/{mj['id']}/image?worker=gpu-pc", content=final).status_code == 200
                  and db.q_one("SELECT status, stage FROM live_jobs WHERE id=?", mj["id"]) == {"status": "pending", "stage": "video"} and db.q_one("SELECT image FROM live_jobs WHERE id=?", mj["id"])["image"].startswith("lvgen_"))
        rep.check("回传合成图:重复回传被拒绝", col.put(f"/api/live/vworker/jobs/{mj['id']}/image", content=final).status_code == 409)
        mj2 = col.get("/api/live/vworker/claim?worker=gpu-pc").json()["job"]
        rep.check("下一个领到的还是第二个场景的合成任务", mj2 and mj2["stage"] == "mask" and mj2["id"] == bids[1])
        col.post(f"/api/live/vworker/jobs/{mj2['id']}/fail", json={"error": "没有抠到主体"})
        rows = {x["id"]: x for x in user.get("/api/live/jobs").json()}
        rep.check("列表:换背景任务显示场景;还没有新图时缩略图用原图;失败显示原因", rows[bids[0]]["scene"] == "白色大理石台面" and rows[bids[0]]["image"] and rows[bids[1]]["thumb"] == g0["orig"] and rows[bids[1]]["status"] == "error" and "抠到" in rows[bids[1]]["error"], str(rows[bids[1]]))
        rep.check("失败重试:改好的背景还在 → 回到合成阶段(不重复花生图费用)", user.post(f"/api/live/jobs/{bids[1]}/retry").json()["stage"] == "mask")
        no_edit = db.q_exec("INSERT INTO live_jobs(image,opts,mode,stage,status,src,gen) VALUES('','{}','zoom','bg','error','bgswap',?)", json.dumps({"kind": "bgswap", "orig": g0["orig"], "scene": "x"})).lastrowid
        rep.check("失败重试:没有改好的背景 → 回到改背景阶段", user.post(f"/api/live/jobs/{no_edit}/retry").json()["stage"] == "bg")
        st = user.get("/api/live/status").json()
        rep.check("状态:合成阶段单独计数", st["video"]["mask_pending"] >= 1, str(st["video"]))
        edit_name = db.q_one("SELECT gen FROM live_jobs WHERE id=?", bids[0])["gen"]["edit"]
        user.delete(f"/api/live/jobs/{no_edit}")
        rep.check("删除:原图还被别的换背景任务用着就留着", (imagegen.IMG_DIR / g0["orig"]).is_file())
        for i in [r["id"] for r in db.q_all("SELECT id FROM live_jobs WHERE src='bgswap'")]:
            user.delete(f"/api/live/jobs/{i}")
        rep.check("删除全部相关任务后:原图和改过背景的图一起清理", not (imagegen.IMG_DIR / g0["orig"]).exists() and not (imagegen.IMG_DIR / edit_name).exists())
    finally:
        api_live.LIVE_DIR, imagegen.IMG_DIR = old_live, old_img
        db.q_exec("DELETE FROM live_jobs")
        db.q_exec("DELETE FROM live_workers")
        db.q_exec("DELETE FROM live_vworkers")
        db.q_exec("DELETE FROM products WHERE name='测试产品'")
        db.q_exec("DELETE FROM drafts WHERE project_id=?", pid)
        db.q_exec("DELETE FROM projects WHERE id=?", pid)
