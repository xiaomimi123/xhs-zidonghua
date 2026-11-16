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
