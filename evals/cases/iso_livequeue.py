"""Live 图排队:按发起人轮流领取(公平)、排队位置 / 预计等待、批次(一次下单一组)、整批下载。
任务都是直接写库造的,成品目录换成临时目录,结束时全部删掉。"""
import io
import tempfile
import zipfile
from pathlib import Path


def _pvt(tag: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("IMG.JPG", b"jpg" + tag.encode())
        z.writestr("IMG.MOV", b"mov" + tag.encode())
    return buf.getvalue()


def run(rep, env):
    from app import api_live as al, db
    user = env.user

    print("\n[Live 排队 / 批次]")
    old_live = al.LIVE_DIR
    tmp = Path(tempfile.mkdtemp())
    al.LIVE_DIR = tmp / "live"
    al.LIVE_DIR.mkdir()
    made = []
    held = db.q_all("SELECT id, status, finished_at FROM live_jobs")   # 先把已有的任务(别的 case 留下的)藏起来,不干扰排队位置 / 完成速度的计算
    db.q_exec("UPDATE live_jobs SET status='held_'||status WHERE status IN ('pending','running')")
    db.q_exec("UPDATE live_jobs SET finished_at=NULL WHERE status='done'")

    def mk(owner, stage="video", status="pending", mode="motion", batch=None, by="", title="", finished=None):
        jid = db.q_exec("INSERT INTO live_jobs(image,opts,created_by,mode,stage,status,owner_id,batch_id,title,finished_at) VALUES('x.png','{}',?,?,?,?,?,?,?,?)",
                        by, mode, stage, status, owner, batch, title, finished).lastrowid
        made.append(jid)
        return jid

    def pick(stages):
        with db.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            return al._fair_pick(c, stages)

    try:
        # ---- 公平领取:A 一次下了 6 个,B 后来下 2 个 → B 不用等 A 的 6 个做完(设备一次只做一个:领一个、做完、再领)----
        al._served.clear()
        a_ids = [mk(901) for _ in range(6)]
        b_ids = [mk(902) for _ in range(2)]
        order = []
        for _ in range(8):
            j = pick(("video",))
            order.append(j)
            db.q_exec("UPDATE live_jobs SET status='done' WHERE id=?", j)
        rep.check("公平领取:A 排了 6 个、B 后来排 2 个,轮流做(A B A B A A A A),不是先把 A 的 6 个做完",
                  order == [a_ids[0], b_ids[0], a_ids[1], b_ids[1]] + a_ids[2:], str(order))
        al._served.clear()
        a2 = [mk(901) for _ in range(3)]
        first = pick(("video",))
        db.q_exec("UPDATE live_jobs SET status='running' WHERE id=?", first)
        b2 = mk(902)   # A 刚被轮到过,这时新来的 B(从没被轮到过)就是下一个
        rep.check("公平领取:A 刚被领过一个,新来的 B 的任务就是下一个", first == a2[0] and pick(("video",)) == b2)
        db.q_exec("UPDATE live_jobs SET status='done' WHERE id IN (?,?)", first, b2)
        rep.check("公平领取:只有一个人排队时先进先出", pick(("video",)) == a2[1])
        rep.check("公平领取:这个阶段没有排队的任务返回空;任务没有发起人(管理员 / 历史)也能被领到",
                  pick(("mask",)) is None and (lambda j: pick(("mask",)) == j)(mk(None, stage="mask")))
        db.q_exec("DELETE FROM live_jobs WHERE id IN (%s)" % ",".join(map(str, made))); made.clear()

        # ---- 领取接口真的走公平顺序(Mac / 视频机共用 _claim)----
        class R:  # 假请求:_beat 只用 client.host
            client = None
        al._served.clear()
        ja = [mk(911, stage="live", mode="zoom") for _ in range(3)]
        jb = mk(912, stage="live", mode="zoom")
        got = []
        for _ in range(4):
            _, r = al._claim("live_workers", "live", R, "fair-test")
            got.append(r["id"])
            db.q_exec("UPDATE live_jobs SET status='done' WHERE id=?", r["id"])
        rep.check("领取接口:按公平顺序给(A、B、A、A)", got == [ja[0], jb, ja[1], ja[2]], str(got))
        db.q_exec("DELETE FROM live_jobs WHERE id IN (%s)" % ",".join(map(str, made))); made.clear()

        # ---- 排队位置 / 预计等待 ----
        # 最近 2 小时做完了 12 个真实动作任务 → 每分钟 0.1 个
        for _ in range(12):
            mk(950, status="done", finished=db.q_one("SELECT datetime('now','-30 minutes') t")["t"])
        a3 = [mk(921) for _ in range(5)]
        b3 = [mk(922) for _ in range(2)]
        qi = al._queue_info()
        rep.check("排队位置:A 的第 1 个前面只有 B 的 1 个(公平轮流),所以是第 2;A 第 3 个 = 3 + min(B 的 2, 3) = 5;B 的第 1 个是第 2",
                  qi[a3[0]]["pos"] == 2 and qi[a3[2]]["pos"] == 5 and qi[b3[0]]["pos"] == 2 and qi[b3[1]]["pos"] == 4, str({k: qi[k] for k in a3 + b3}))
        rep.check("预计等待:按最近 2 小时的完成速度估(0.1 个 / 分钟 → 第 5 位约 50 分钟);样本少于 3 个时不估(None);只算排队中的",
                  qi[a3[2]]["eta"] == 50 and a3[0] in qi and not [k for k in qi if k in made and False])
        db.q_exec("DELETE FROM live_jobs WHERE status='done' AND owner_id=950")
        qi2 = al._queue_info([a3[0]])
        rep.check("预计等待:最近没有完成记录时只给位置不给时间;ids 参数只算指定的", qi2[a3[0]]["eta"] is None and list(qi2) == [a3[0]])

        # ---- 批次:一次下单一个批次号(五个下单入口都记);列表 / 展开 / 整批下载 / 重试失败 / 清完成 ----
        bid = al._new_batch()
        rep.check("批次号:每次下单递增", al._new_batch() == bid and mk(930, batch=bid) and al._new_batch() == bid + 1)
        j_done1 = mk(930, status="done", batch=bid + 1, title="糕点 一", by="小A")
        j_done2 = mk(930, status="done", batch=bid + 1, title="糕点 二", by="小A")
        j_err = mk(930, status="error", batch=bid + 1, title="糕点 三", by="小A")
        j_run = mk(930, status="pending", batch=bid + 1, title="糕点 四", by="小A")
        for j in (j_done1, j_done2):
            (al.LIVE_DIR / f"{j}.zip").write_bytes(_pvt(str(j)))
        b = bid + 1
        lst = user.get("/api/live/batches?limit=100").json()
        me = next((x for x in lst if x["id"] == b), None)
        rep.check("批次列表:总数 / 完成 / 失败 / 排队、能下载的个数、标题和缩略图、发起人",
                  me and me["total"] == 4 and me["done"] == 2 and me["err"] == 1 and me["pending"] == 1 and me["ready"] == 2 and me["title"] == "糕点 一" and me["by"] == "小A" and len(me["thumbs"]) == 4, str(me))
        rep.check("批次列表:按状态筛(run 有没做完的 / err 有失败的 / done 全做完且没失败的)",
                  any(x["id"] == b for x in user.get("/api/live/batches?status=run").json()) and any(x["id"] == b for x in user.get("/api/live/batches?status=err").json())
                  and not any(x["id"] == b for x in user.get("/api/live/batches?status=done").json()) and all(x["pending"] + x["running"] > 0 for x in user.get("/api/live/batches?status=run").json()))
        rep.check("批次列表:按发起人筛", [x["id"] for x in user.get("/api/live/batches?by=小A").json()] == [b])
        smb = user.get("/api/live/jobs/summary").json()["batches"]
        rep.check("任务页汇总:批次数(全部 / 进行中 / 已完成 / 有失败)和批次列表的筛选口径一致",
                  smb["all"] == len(user.get("/api/live/batches?limit=100").json()) and all(smb[k] == len(user.get(f"/api/live/batches?limit=100&status={k}").json()) for k in ("run", "done", "err")), str(smb))
        jobs = user.get(f"/api/live/batches/{b}/jobs").json()
        rep.check("批次展开:返回这一批全部任务(带排队位置)", [x["id"] for x in jobs] == [j_done1, j_done2, j_err, j_run] and jobs[3]["queue"] and jobs[0]["has_result"] and not jobs[2]["has_result"], str(jobs[3]))
        rep.check("批次:不存在 404", user.get("/api/live/batches/99999/jobs").status_code == 404 and user.get("/api/live/batches/99999/download").status_code == 404)
        z = user.get(f"/api/live/batches/{b}/download")
        zf = zipfile.ZipFile(io.BytesIO(z.content)) if z.status_code == 200 else None
        names = sorted(zf.namelist()) if zf else []
        rep.check("整批下载:只含做好的两个,每个一个 .pvt 文件夹,内容对得上;临时文件发完就删",
                  z.status_code == 200 and len(names) == 4 and all(".pvt/" in n for n in names) and "attachment" in z.headers.get("content-disposition", "")
                  and zf.read(next(n for n in names if n.endswith("IMG.JPG") and f"_{j_done1}_" in n)) == b"jpg" + str(j_done1).encode()
                  and not list(Path(tempfile.gettempdir()).glob("lvbatch_*.zip")), str(names))
        z2 = user.get(f"/api/live/batches/{b}/download?ids={j_done2},{j_err},abc")
        rep.check("整批下载:ids 只下选中的(没做好的自动跳过);一个都没做好 404",
                  z2.status_code == 200 and len(zipfile.ZipFile(io.BytesIO(z2.content)).namelist()) == 2 and user.get(f"/api/live/batches/{b}/download?ids={j_err}").status_code == 404)
        rr = user.post(f"/api/live/batches/{b}/retry").json()
        rep.check("批次重试失败:只重试失败的", rr["retried"] == 1 and db.q_one("SELECT status FROM live_jobs WHERE id=?", j_err)["status"] == "pending"
                  and db.q_one("SELECT status FROM live_jobs WHERE id=?", j_run)["status"] == "pending")
        cd = user.post(f"/api/live/batches/{b}/clear_done").json()
        rep.check("批次清理已完成:删记录也删成品文件,没做完的留着", cd["deleted"] == 2 and not (al.LIVE_DIR / f"{j_done1}.zip").exists()
                  and db.q_one("SELECT COUNT(*) n FROM live_jobs WHERE batch_id=?", b)["n"] == 2)

        # ---- 任务列表带批次和排队信息 ----
        lj = {x["id"]: x for x in user.get("/api/live/jobs?status=run&limit=500").json()}
        rep.check("任务列表:每个任务带批次号;排队中的带位置 / 预计等待,生成中的没有", lj[j_run]["batch"] == b and lj[j_run]["queue"]["pos"] >= 1)

        # ---- 迁移补批次:同一个人同一类 10 分钟内算一批,隔久了 / 换人 / 换类另算 ----
        from app.db import _m017_live_batch
        base = [("2026-01-01 10:00:00", "甲", "gen"), ("2026-01-01 10:05:00", "甲", "gen"), ("2026-01-01 10:30:00", "甲", "gen"),
                ("2026-01-01 10:06:00", "乙", "gen"), ("2026-01-01 10:07:00", "甲", "upload")]
        old_ids = []
        for t, who, src in base:
            old_ids.append(db.q_exec("INSERT INTO live_jobs(image,opts,created_by,mode,stage,status,src,created) VALUES('x.png','{}',?, 'zoom','live','done',?,?)", who, src, t).lastrowid)
        made.extend(old_ids)
        db.q_exec("UPDATE live_jobs SET batch_id=NULL WHERE id IN (%s)" % ",".join(map(str, old_ids)))
        with db.conn() as c:
            _m017_live_batch(c)
        bs = [db.q_one("SELECT batch_id FROM live_jobs WHERE id=?", i)["batch_id"] for i in old_ids]
        rep.check("迁移补批次:甲 10:00 和 10:05 同一批,10:30 另一批;乙、甲的上传各自一批;迁移可重复执行",
                  bs[0] == bs[1] and bs[2] != bs[0] and len({bs[0], bs[2], bs[3], bs[4]}) == 4 and all(isinstance(x, int) for x in bs), str(bs))
    finally:
        db.q_exec("DELETE FROM live_jobs WHERE owner_id IN (901,902,911,912,921,922,930,950)")
        if made:
            db.q_exec("DELETE FROM live_jobs WHERE id IN (%s)" % ",".join(map(str, made)))
        for h in held:
            db.q_exec("UPDATE live_jobs SET status=?, finished_at=? WHERE id=?", h["status"], h["finished_at"], h["id"])
        al._served.clear()
        al.LIVE_DIR = old_live
