"""后台线程出错的日志(限频)+ 店铺采集端掉线 / 数据太久没更新的提醒。"""
import io
import contextlib


def run(rep, env):
    from app import db, logx, qianfan

    print("\n[出错日志与同步提醒]")
    logx._seen.clear()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        a = logx.warn("测试处", ValueError("boom"), trace=False)
        b = logx.warn("测试处", ValueError("boom"), trace=False)
        c = logx.warn("测试处", ValueError("另一个错"), trace=False)
        try:
            1 / 0
        except ZeroDivisionError as e:
            d = logx.warn("带堆栈", e)
    out = buf.getvalue()
    rep.check("出错日志:同一处同样的错 10 分钟内只记一次,换了错误内容或地方照记;带「[警告]」前缀,堆栈里能看到出错的行",
              a and not b and c and d and out.count("[警告]") == 3 and "ZeroDivisionError" in out and "测试处:ValueError: boom" in out, out[:200])

    db.save_settings({"qf_enabled": "1", "qf_account_hours": "24"})
    db.q_exec("DELETE FROM qf_shops")
    db.q_exec("DELETE FROM qf_snapshots WHERE shop_id LIKE 'al_%'")
    ago = lambda h: f"datetime('now','-{h} hours')"
    for sid, seen in (("al_ok", 0.1), ("al_off", 5), ("al_stale", 0.2), ("al_new", 0.1)):
        db.q_exec(f"INSERT INTO qf_shops(shop_id,name,first_seen,last_seen) VALUES(?,?,{ago(100)},{ago(seen)})", sid, sid.replace("al_", "店"))
    for sid, h in (("al_ok", 2), ("al_off", 6), ("al_stale", 60)):
        db.q_exec(f"INSERT INTO qf_snapshots(kind,shop_id,snap_at,data) VALUES('account',?,{ago(h)},'{{}}')", sid)
    al = {x["shop"]: x for x in qianfan.sync_alerts()}
    rep.check("同步提醒:采集端 5 小时没来的店铺提醒「掉线」;在线但数据 60 小时没更新(>2×同步间隔)的提醒「没更新」;正常的和还没有数据的新店不提醒",
              set(al) == {"店off", "店stale"} and al["店off"]["kind"] == "offline" and al["店stale"]["kind"] == "stale" and "5 小时" in al["店off"]["msg"], str(al))
    db.save_settings({"qf_enabled": "0"})
    rep.check("同步总开关关掉后不提醒", qianfan.sync_alerts() == [])
    db.save_settings({"qf_enabled": "1"})
    db.q_exec("DELETE FROM qf_shops WHERE shop_id LIKE 'al_%'")
    db.q_exec("DELETE FROM qf_snapshots WHERE shop_id LIKE 'al_%'")

    # ---- 成品库不能有数量上限(以前写死只取最新 500 篇,页面上的总数和各筛选的数量都不对) ----
    from app import api_user
    pid = db.q_exec("INSERT INTO projects(track,keywords) VALUES('美食','[\"成品库上限测试\"]')").lastrowid
    with db.conn() as c:
        c.executemany("INSERT INTO drafts(project_id,title,status) VALUES(?,?,'done')", [(pid, f"上限测试稿{i}") for i in range(520)])
    try:
        got = api_user._library(pid=pid, brief=True)
        rep.check("成品库:一个任务里有 520 篇成品时全部返回(不再只取最新 500 篇)", len(got) == 520 and got[0]["id"] > got[-1]["id"], str(len(got)))
    finally:
        db.q_exec("DELETE FROM drafts WHERE project_id=?", pid)
        db.q_exec("DELETE FROM projects WHERE id=?", pid)

    # ---- 生图通用风格:物品必须完整入镜(以前写「构图随意」,杯子被画面边缘切掉、杯把出画面) ----
    from app import covers, worker
    pr = worker._image_prompt("", "桌上一杯拿铁", True)
    rep.check("生图风格:要求每件物品完整入镜、不被边缘切掉、杯把杯口完整、物品留间距互不穿插;不再写「构图随意」;Live 拍法里不再要求「遮挡」",
              "完整入镜" in pr and "杯把和杯口完整可见" in pr and "互不穿插重叠" in pr and "构图随意" not in pr
              and not any("遮挡" in x for x in covers.LOOK_STATES) and any("不穿插" in x for x in covers.LOOK_STATES))

    # ---- 调外部服务失败:给页面一句看得懂的话(以前是「Internal Server Error」) ----
    from fastapi import FastAPI as _F
    from fastapi.testclient import TestClient as _TC
    from app import health as _h
    raw402 = 'DeepSeek 返回 402:{"error":{"message":"Insufficient Balance"}}'
    rep.check("错误翻译:余额不足 / Key 无效 / 限流翻成员工看得懂的话;其他错误原样",
              "余额不足" in _h.friendly(raw402) and "Key" in _h.friendly("DeepSeek 返回 401:invalid api key") and "限流" in _h.friendly("DeepSeek 返回 429:x")
              and _h.friendly("连不上 DeepSeek:ConnectError") == "连不上 DeepSeek:ConnectError")
    _a = _F()
    _a.add_exception_handler(RuntimeError, _h.service_error)

    @_a.post("/boom")
    def _boom():
        raise RuntimeError(raw402)
    r = _TC(_a, raise_server_exceptions=False).post("/boom")
    rep.check("接口里调大模型失败(余额不足):返回 502 和一句看得懂的话,不再是 500「Internal Server Error」", r.status_code == 502 and "余额不足" in r.json()["detail"], r.text[:120])
