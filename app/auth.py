"""鉴权:管理端密码登录、用户端访问密码(可选)、采集端 Token。
登录态是带签名和过期时间的 Cookie,签名密钥保存在 data/secret.key。"""
import contextvars
import hashlib
import hmac
import os
import secrets
import time
from fastapi import HTTPException, Request
from .db import DATA, get_settings, save_settings, q_one

SECRET_FILE = DATA / "secret.key"
INIT_PW_FILE = DATA / "初始管理员密码.txt"
SESSION_DAYS = 7
_fails: dict[str, list[float]] = {}  # ip -> 最近失败时间,用于登录限流


def _secret() -> bytes:
    if not SECRET_FILE.exists():
        SECRET_FILE.write_bytes(secrets.token_bytes(32))
    return SECRET_FILE.read_bytes()


def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    return salt.hex() + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000).hex()


def check_pw(pw: str, stored: str) -> bool:
    try:
        salt, h = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex(), h)


def make_token(role: str) -> str:
    msg = f"{role}.{int(time.time()) + SESSION_DAYS * 86400}"
    return msg + "." + hmac.new(_secret(), msg.encode(), "sha256").hexdigest()


def _valid(token: str, role: str) -> bool:
    try:
        msg, sig = token.rsplit(".", 1)
        r, exp = msg.split(".")
    except ValueError:
        return False
    return (r == role and int(exp) > time.time()
            and hmac.compare_digest(sig, hmac.new(_secret(), msg.encode(), "sha256").hexdigest()))


def is_admin(request: Request) -> bool:
    return _valid(request.cookies.get("adm", ""), "admin")


def require_admin(request: Request):
    if not is_admin(request):
        raise HTTPException(401, "需要管理员登录")


# 当前请求的登录人(员工 dict 或 {"name": "管理员"});建任务时用来记发起人
CURRENT = contextvars.ContextVar("current_user", default=None)


def _role_of(token: str):
    """签名正确且没过期时返回 token 里的角色串,否则 None。"""
    try:
        msg, sig = token.rsplit(".", 1)
        r, exp = msg.split(".")
        ok = int(exp) > time.time() and hmac.compare_digest(sig, hmac.new(_secret(), msg.encode(), "sha256").hexdigest())
    except ValueError:
        return None
    return r if ok else None


def make_staff_token(staff: dict) -> str:
    return make_token(f"staff:{staff['id']}:{staff['token_ver']}")


def current_staff(request: Request):
    """Cookie 对应的员工(账号存在、没停用、会话版本没被作废),否则 None。"""
    role = _role_of(request.cookies.get("usr", "")) or ""
    parts = role.split(":")
    if len(parts) != 3 or parts[0] != "staff":
        return None
    try:
        sid, ver = int(parts[1]), int(parts[2])
    except ValueError:
        return None
    s = q_one("SELECT id, username, name, enabled, token_ver FROM staff WHERE id=?", sid)
    return s if s and s["enabled"] and s["token_ver"] == ver else None


def staff_mode() -> bool:
    """建过员工账号(哪怕都停用了)就必须登录:避免全部停用后前端反而对所有人开放。"""
    return bool(q_one("SELECT 1 AS x FROM staff LIMIT 1"))


def login_required() -> bool:
    return staff_mode() or bool(get_settings()["user_password_hash"])


def user_ok(request: Request) -> bool:
    if is_admin(request) or not login_required():
        return True
    if current_staff(request):
        return True
    return not staff_mode() and _valid(request.cookies.get("usr", ""), "user")  # 旧的通用访问密码


async def require_user(request: Request):
    if not user_ok(request):
        raise HTTPException(401, "请先登录")
    s = current_staff(request)
    CURRENT.set(s if s else ({"name": "管理员", "username": "admin"} if is_admin(request) else None))


def require_collector(request: Request):
    token = get_settings()["collector_token"]
    got = request.headers.get("authorization", "").removeprefix("Bearer ").strip() or request.headers.get("x-token", "")
    if not token or not hmac.compare_digest(got, token):
        raise HTTPException(401, "采集端 token 无效")


def throttle(request: Request, ok: bool):
    """同一 IP 10 分钟内失败 5 次就锁定。ok=None 时只检查不记录。"""
    ip = request.client.host if request.client else "?"
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < 600]
    if len(recent) >= 5:
        raise HTTPException(429, "尝试次数过多,请 10 分钟后再试")
    if ok is False:
        recent.append(now)
    _fails[ip] = recent


def bootstrap():
    """首次启动:生成管理员密码与采集端 Token。
    管理员密码优先取环境变量 ADMIN_PASSWORD;否则随机生成并写入 data/初始管理员密码.txt。"""
    s, upd = get_settings(), {}
    env_pw = os.environ.get("ADMIN_PASSWORD", "").strip()
    if env_pw and not check_pw(env_pw, s["admin_password_hash"]):
        upd["admin_password_hash"] = hash_pw(env_pw)
    elif not s["admin_password_hash"]:
        pw = secrets.token_urlsafe(9)
        upd["admin_password_hash"] = hash_pw(pw)
        INIT_PW_FILE.write_text(f"管理端地址:/admin\n初始管理员密码:{pw}\n登录后请在「账号安全」里修改,并删除本文件。\n", encoding="utf-8")
        print(f"\n[工作台] 已生成初始管理员密码,见 {INIT_PW_FILE}\n")
    if not s["collector_token"]:
        upd["collector_token"] = secrets.token_urlsafe(24)
    if upd:
        save_settings(upd)
