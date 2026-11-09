"""人员管理:员工账号、前端登录、停用/重置密码/踢下线让旧会话失效、任务记录发起人、旧的通用访问密码。
放在最后跑,并在结束时清掉员工账号,让前端回到免登录,不影响别的用例。"""
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ..common import _raises2


def run(rep, env):
    from app import api_admin as aa, auth, db

    app, q = env.app, env.q
    print("\n[人员管理]")
    anon = TestClient(app)
    s0 = anon.get("/api/session").json()
    rep.check("没建员工账号:前端免登录", s0["ok"] and not s0["need_password"] and not s0["staff_mode"], str(s0))

    def add(u, pw="pass123", name=""):
        return aa.staff_add(aa.StaffIn(username=u, name=name, password=pw))["id"]

    rep.check("新增:密码太短拒绝", _raises2(lambda: add("zhangsan", "123")))
    rep.check("新增:账号含空格/太短拒绝", _raises2(lambda: add("a b")) and _raises2(lambda: add("x")))
    sid = add("zhangsan", "pass123", "张三")
    rep.check("新增:账号不能重复(不分大小写)", _raises2(lambda: add("ZhangSan")))
    row = db.q_one("SELECT password_hash FROM staff WHERE id=?", sid)
    rep.check("密码只存哈希", row["password_hash"] and "pass123" not in row["password_hash"])

    s1 = anon.get("/api/session").json()
    rep.check("建了员工账号:前端必须登录", not s1["ok"] and s1["staff_mode"] and anon.get("/api/tracks").status_code == 401, str(s1))

    c = TestClient(app)
    bad = c.post("/api/login", json={"username": "zhangsan", "password": "wrong"})
    nouser = c.post("/api/login", json={"username": "nobody", "password": "pass123"})
    rep.check("登录失败:密码错或账号不存在,提示一样(不泄露账号是否存在)", bad.status_code == 400 and nouser.status_code == 400 and bad.json() == nouser.json(), f"{bad.json()} {nouser.json()}")
    rep.check("只填密码不填账号:拒绝", c.post("/api/login", json={"password": "pass123"}).status_code == 400)
    auth._fails.clear()
    ok = c.post("/api/login", json={"username": "ZHANGSAN", "password": "pass123"})  # 账号不分大小写
    sess = c.get("/api/session").json()
    rep.check("登录成功:能访问,会话里有姓名", ok.status_code == 200 and c.get("/api/tracks").status_code == 200 and sess["user"]["name"] == "张三", str(sess))
    rep.check("登录后记录最近登录时间", db.q_one("SELECT last_login FROM staff WHERE id=?", sid)["last_login"] != "")

    pj = c.post("/api/projects", json={"track": "美食", "keywords": ["早餐"], "direction": ""}).json()["id"]
    rep.check("任务记录发起人账号", db.q_one("SELECT created_by FROM projects WHERE id=?", pj)["created_by"] == "zhangsan")
    lst = aa.staff_list()
    rep.check("人员管理列表带上发起任务数", lst["items"][0]["projects"] == 1 and lst["items"][0]["username"] == "zhangsan", str(lst["items"]))

    # 停用:立刻失效,不能登录;启用后旧会话仍然无效,要重新登录
    aa.staff_edit(sid, aa.StaffPatch(enabled=False))
    rep.check("停用:已登录的会话立刻失效", c.get("/api/tracks").status_code == 401)
    r403 = TestClient(app).post("/api/login", json={"username": "zhangsan", "password": "pass123"})
    rep.check("停用:不能再登录,提示联系管理员", r403.status_code == 403 and "停用" in r403.json()["detail"], str(r403.json()))
    aa.staff_edit(sid, aa.StaffPatch(enabled=True))
    rep.check("重新启用:旧会话仍然无效", c.get("/api/tracks").status_code == 401)
    c = TestClient(app)
    c.post("/api/login", json={"username": "zhangsan", "password": "pass123"})
    rep.check("重新启用后重新登录可用", c.get("/api/tracks").status_code == 200)

    aa.staff_kick(sid)
    rep.check("踢下线:当前会话失效", c.get("/api/tracks").status_code == 401)

    c = TestClient(app)
    c.post("/api/login", json={"username": "zhangsan", "password": "pass123"})
    rep.check("重置密码:密码太短拒绝", _raises2(lambda: aa.staff_reset_pw(sid, aa.StaffPw(password="12"))))
    aa.staff_reset_pw(sid, aa.StaffPw(password="newpass1"))
    rep.check("重置密码:旧会话失效、旧密码不能用", c.get("/api/tracks").status_code == 401 and TestClient(app).post("/api/login", json={"username": "zhangsan", "password": "pass123"}).status_code == 400)
    c = TestClient(app)
    rep.check("重置密码:新密码可以登录", c.post("/api/login", json={"username": "zhangsan", "password": "newpass1"}).status_code == 200)

    # 全部停用后前端也不能对所有人开放
    aa.staff_edit(sid, aa.StaffPatch(enabled=False))
    rep.check("所有员工都停用:前端仍然需要登录(不会反而开放)", TestClient(app).get("/api/tracks").status_code == 401)
    aa.staff_edit(sid, aa.StaffPatch(enabled=True))

    # 退出登录
    c = TestClient(app)
    c.post("/api/login", json={"username": "zhangsan", "password": "newpass1"})
    c.post("/api/logout")
    rep.check("退出登录后不能访问", c.get("/api/tracks").status_code == 401)

    # 登录限流:同一 IP 连续输错会被锁
    auth._fails.clear()
    t = TestClient(app)
    codes = [t.post("/api/login", json={"username": "zhangsan", "password": "x"}).status_code for _ in range(6)]
    rep.check("连续输错密码被限流", codes[-1] == 429, str(codes))
    auth._fails.clear()

    # 删除:账号不能再登录;历史任务的发起人保留;全部删掉后前端回到免登录
    aa.staff_delete(sid)
    rep.check("删除后不能登录,历史任务发起人保留", TestClient(app).post("/api/login", json={"username": "zhangsan", "password": "newpass1"}).status_code == 400
              and db.q_one("SELECT created_by FROM projects WHERE id=?", pj)["created_by"] == "zhangsan")
    rep.check("员工全部删除:前端回到免登录", TestClient(app).get("/api/tracks").status_code == 200)

    # 旧的通用访问密码(没建员工账号时)仍然可用
    auth._fails.clear()
    db.save_settings({"user_password_hash": auth.hash_pw("legacy123")})
    try:
        lc = TestClient(app)
        rep.check("通用访问密码:未登录被拒", lc.get("/api/tracks").status_code == 401 and lc.get("/api/session").json()["need_password"])
        rep.check("通用访问密码:只填密码即可登录", lc.post("/api/login", json={"password": "legacy123"}).status_code == 200 and lc.get("/api/tracks").status_code == 200)
        sid2 = add("lisi")
        rep.check("建了员工账号后通用密码不再生效", lc.get("/api/tracks").status_code == 401)
        aa.staff_delete(sid2)
    finally:
        db.save_settings({"user_password_hash": ""})
        auth._fails.clear()
    db.q_exec("DELETE FROM projects WHERE id=?", pj)
