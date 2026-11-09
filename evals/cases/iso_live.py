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
    finally:
        api_live.LIVE_DIR, imagegen.IMG_DIR = old_live, old_img
        db.q_exec("DELETE FROM live_jobs")
        db.q_exec("DELETE FROM live_workers")
        db.q_exec("DELETE FROM drafts WHERE project_id=?", pid)
        db.q_exec("DELETE FROM projects WHERE id=?", pid)
