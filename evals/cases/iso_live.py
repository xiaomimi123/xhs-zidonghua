"""Live 图任务队列:选图下单 → Mac 端领取 → 回传成品 → 下载 / 预览 → 失败重试 / 超时退回 / 清理。
成品目录和图片目录都换成临时目录,不碰真实的 data/。"""
import io
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

        # Mac 端
        from fastapi.testclient import TestClient
        anon = TestClient(env.app)
        rep.check("Mac 端接口:没有 Token 拒绝", anon.get("/api/live/worker/claim").status_code == 401)
        st = user.get("/api/live/status").json()
        rep.check("状态:还没有 Mac 连接时显示离线", not st["online"] and st["pending"] == 2, str(st))
        c = col.get("/api/live/worker/claim?worker=mac-test").json()["job"]
        rep.check("领任务:先进先出,返回参数和原图地址", c and c["id"] == jid and c["opts"]["zoom"] == 1.08 and c["source"].endswith(f"/{jid}/source"), str(c))
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
    finally:
        api_live.LIVE_DIR, imagegen.IMG_DIR = old_live, old_img
        db.q_exec("DELETE FROM live_jobs")
        db.q_exec("DELETE FROM live_workers")
        db.q_exec("DELETE FROM live_vworkers")
        db.q_exec("DELETE FROM drafts WHERE project_id=?", pid)
        db.q_exec("DELETE FROM projects WHERE id=?", pid)
