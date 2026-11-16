"""设置里的 Key 加密存储、管理员登录版本(改密码后旧登录失效)、外部服务调用状态(失败原因要能在管理端看到)。"""


def run(rep, env):
    from fastapi.testclient import TestClient
    from app import auth, db, health, llm, vault

    print("\n[Key 加密 / 管理员会话 / 服务状态]")
    db.save_settings({"deepseek_api_key": "sk-test-123", "image_api_key": "img-key-9", "deepseek_model": "m1"})
    raw = {r["key"]: r["value"] for r in db.q_all("SELECT key,value FROM settings")}
    rep.check("Key 存进库里是密文(带前缀、看不到原文),普通设置不加密;读出来自动还原",
              raw["deepseek_api_key"].startswith(vault.PREFIX) and "sk-test-123" not in raw["deepseek_api_key"] and raw["image_api_key"].startswith(vault.PREFIX)
              and raw["deepseek_model"] == "m1" and db.get_settings()["deepseek_api_key"] == "sk-test-123" and db.get_settings()["image_api_key"] == "img-key-9")
    db.q_exec("INSERT OR REPLACE INTO settings VALUES('vision_api_key','plain-old')")
    db.seal_existing_settings()
    raw2 = db.q_one("SELECT value FROM settings WHERE key='vision_api_key'")["value"]
    db.seal_existing_settings()
    rep.check("旧的明文 Key 启动时自动转成密文,读出来不变;重复执行不会重复加密",
              raw2.startswith(vault.PREFIX) and db.get_settings()["vision_api_key"] == "plain-old" and db.q_one("SELECT value FROM settings WHERE key='vision_api_key'")["value"] == raw2)
    db.save_settings({"deepseek_api_key": ""})
    rep.check("清空 Key 存的是空串(不是密文),读出仍为空", db.q_one("SELECT value FROM settings WHERE key='deepseek_api_key'")["value"] == "" and db.get_settings()["deepseek_api_key"] == "")
    db.q_exec("UPDATE settings SET value=? WHERE key='image_api_key'", vault.PREFIX + "损坏的密文")
    rep.check("密文解不开时读出空串,不报错", db.get_settings()["image_api_key"] == "")
    db.save_settings({"image_api_key": "", "vision_api_key": ""})

    # ---- 管理员登录版本 ----
    from fastapi import FastAPI
    from app import api_admin as aa
    app = FastAPI()
    for r in (aa.public, aa.router):
        app.include_router(r)
    pw0 = "adm-old-pass-1"
    db.save_settings({"admin_password_hash": auth.hash_pw(pw0)})
    auth._fails.clear()
    c1, c2 = TestClient(app), TestClient(app)
    ok = c1.post("/api/admin/login", json={"password": pw0}).status_code == 200 and c2.post("/api/admin/login", json={"password": pw0}).status_code == 200
    rep.check("管理员在两台设备上登录,都能进管理端", ok and c1.get("/api/admin/settings").status_code == 200 and c2.get("/api/admin/settings").status_code == 200)
    r = c1.post("/api/admin/password", json={"old": pw0, "new": "adm-new-pass-2"})
    rep.check("改密码后:改密码的这台继续可用(换了新会话),另一台的旧登录立刻失效",
              r.status_code == 200 and c1.get("/api/admin/settings").status_code == 200 and c2.get("/api/admin/settings").status_code == 401, str((r.status_code, r.text[:80])))
    rep.check("旧版本号签发的登录凭证无效;设置接口不暴露登录版本号和服务状态", TestClient(app, cookies={"adm": auth.make_token("admin")}).get("/api/admin/settings").status_code == 401
              and "admin_token_ver" not in c1.get("/api/admin/settings").json() and "svc_health" not in c1.get("/api/admin/settings").json())
    rep.check("管理员改密码输错原密码:被拒绝,不会作废现有登录", c1.post("/api/admin/password", json={"old": "wrong", "new": "whatever-123"}).status_code == 400 and c1.get("/api/admin/settings").status_code == 200)
    auth._fails.clear()

    # ---- 服务调用状态 ----
    health._state.clear()
    db.q_exec("DELETE FROM settings WHERE key='svc_health'")
    o_post = llm.httpx.post

    class _R:
        def __init__(self, code, text=""):
            self.status_code, self.text = code, text
        def json(self):
            return {"choices": [{"message": {"content": "{\"a\": 1}"}}]}
    db.save_settings({"deepseek_api_key": "k", "deepseek_base_url": "http://x"})
    try:
        llm.httpx.post = lambda *a, **k: _R(401, "invalid api key")
        try:
            llm.chat("s", "u")
        except RuntimeError:
            pass
        st = {x["key"]: x for x in health.status()}
        rep.check("大模型调用失败:记下失败原因和时间,概览里标为「失败中」;重启后(清掉内存)仍能从设置里读到",
                  st["llm"]["failing"] and "401" in st["llm"]["err"] and (health._state.clear() or True) and {x["key"]: x for x in health.status()}["llm"]["failing"])
        llm.httpx.post = lambda *a, **k: _R(200)
        rep.check("下一次成功后,失败标记解除", llm.chat("s", "u") == {"a": 1} and not {x["key"]: x for x in health.status()}["llm"]["failing"])
        db.save_settings({"deepseek_api_key": ""})
        try:
            llm.chat("s", "u")
        except RuntimeError:
            pass
        rep.check("没填 Key(「请先…」)不算服务故障", not {x["key"]: x for x in health.status()}["llm"]["failing"])
        ov = c1.get("/api/admin/overview").json()
        rep.check("管理端概览带服务状态和备份状态", {x["key"] for x in ov["services"]} == {"llm", "image", "vision"} and "backup" in ov)
    finally:
        llm.httpx.post = o_post
        db.save_settings({"deepseek_api_key": "", "deepseek_base_url": "https://api.deepseek.com"})
