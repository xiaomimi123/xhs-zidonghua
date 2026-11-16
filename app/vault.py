"""设置里的 API Key / 采集端 Token 加密存储:库里只有密文,密钥来自 data/secret.key(和数据库分开存放)。
这样数据库备份被拷到别处(第二份备份、发给别人排查问题)时,里面的 Key 不会跟着泄露。
密文带 "enc:v1:" 前缀;没有前缀的旧明文照常读出,下次保存或启动时自动转成密文。"""
import base64
import hashlib
import hmac
import secrets

PREFIX = "enc:v1:"
ENC_KEYS = {"deepseek_api_key", "image_api_key", "vision_api_key", "collector_token"}
_fernet = None


def master() -> bytes:
    from . import db   # 延迟导入:db 也要用本模块
    f = db.DATA / "secret.key"
    if not f.exists():
        f.write_bytes(secrets.token_bytes(32))
    return f.read_bytes()


def _f():
    global _fernet
    if _fernet is None:
        from cryptography.fernet import Fernet
        _fernet = Fernet(base64.urlsafe_b64encode(hmac.new(master(), b"settings-vault-v1", hashlib.sha256).digest()))
    return _fernet


def seal(v: str) -> str:
    return v if not v or v.startswith(PREFIX) else PREFIX + _f().encrypt(v.encode()).decode()


def unseal(v: str) -> str:
    """解不开(密钥文件换了 / 密文损坏)返回空串,让页面提示重新填写,而不是让整个程序报错。"""
    if not v or not v.startswith(PREFIX):
        return v
    try:
        return _f().decrypt(v[len(PREFIX):].encode()).decode()
    except Exception:
        print("[保险箱] 有一项设置解不开(secret.key 被换过?),请在管理端重新填写", flush=True)
        return ""
