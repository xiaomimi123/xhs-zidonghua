"""外部服务(大模型 / 生图 / 看图)最近一次调用的结果:成功时间、最近一次失败的原因和时间。
管理端概览据此在 Key 失效、余额用完、接口连不上时给出提示,而不是让员工只看到「没反应」。
记在内存里,失败时同时存进设置(重启后还能看到);成功只在状态变化时存。"""
import functools
import json
import threading
import time

NAMES = {"llm": "DeepSeek 大模型", "image": "生图接口", "vision": "看图接口"}
_state: dict[str, dict] = {}
_lock = threading.Lock()


def _load():
    if _state:
        return
    from .db import q_one
    r = q_one("SELECT value FROM settings WHERE key='svc_health'")
    try:
        _state.update(json.loads(r["value"]) if r else {})
    except ValueError:
        pass
    for k in NAMES:
        _state.setdefault(k, {"ok_at": 0, "err_at": 0, "err": ""})


def _save():
    from .db import save_settings
    try:
        save_settings({"svc_health": json.dumps(_state, ensure_ascii=False)})
    except Exception:
        pass


def record(svc: str, err: str | None):
    """err 为 None 表示这次调用成功。没填 Key 之类「请先…」的提示不算服务故障。"""
    with _lock:
        _load()
        st = _state[svc]
        now = int(time.time())
        if err is None:
            changed = st["err_at"] > st["ok_at"]
            st["ok_at"] = now
            if changed:
                _save()
        elif not err.startswith("请先"):
            st["err_at"], st["err"] = now, err[:300]
            _save()


def tracked(svc: str):
    def deco(fn):
        @functools.wraps(fn)
        def wrap(*a, **kw):
            try:
                out = fn(*a, **kw)
            except Exception as e:
                record(svc, str(e))
                raise
            record(svc, None)
            return out
        return wrap
    return deco


def friendly(err: str) -> str:
    """把接口返回的原始错误翻成员工看得懂的话(原文放在后面方便管理员排查)。"""
    e = err or ""
    low = e.lower()
    if "402" in e or "insufficient" in low and "balance" in low or "余额不足" in e:
        return "大模型 / 生图服务的账户余额不足,请联系管理员充值后再试"
    if "返回 401" in e or "invalid api key" in low or "authentication" in low:
        return "接口 Key 无效或已过期,请联系管理员在「接口配置」里检查"
    if "返回 429" in e or "rate limit" in low:
        return "调用太频繁,服务商暂时限流了,请稍后再试"
    return e


async def service_error(request, exc: RuntimeError):
    """调大模型 / 生图服务失败(余额不足、Key 失效、连不上…)时,给页面一句看得懂的话,而不是「Internal Server Error」。"""
    from fastapi.responses import JSONResponse
    from . import logx
    logx.warn(f"{request.method} {request.url.path}", exc, trace=False)
    return JSONResponse({"detail": friendly(str(exc))}, status_code=502)


def status() -> list[dict]:
    with _lock:
        _load()
        return [{"key": k, "name": NAMES[k], "ok_at": v["ok_at"], "err_at": v["err_at"], "err": v["err"], "hint": friendly(v["err"]),
                 "failing": v["err_at"] > v["ok_at"]} for k, v in _state.items() if k in NAMES]
