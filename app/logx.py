"""后台线程里「出错了但不能中断」的地方统一用这个记一行日志(同一处同样的错 10 分钟内只记一次,免得循环里刷屏)。
docker logs xhs-workbench 里搜「[警告]」就能看到。"""
import time
import traceback

_seen: dict[tuple, float] = {}
GAP = 600


def warn(where: str, exc: BaseException, trace: bool = True) -> bool:
    """记一条警告,返回这次是否真的输出了(被限频跳过返回 False)。"""
    key = (where, type(exc).__name__, str(exc)[:80])
    now = time.time()
    if now - _seen.get(key, 0) < GAP:
        return False
    if len(_seen) > 500:
        _seen.clear()
    _seen[key] = now
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[-3:]).strip() if trace else f"{type(exc).__name__}: {exc}"
    print(f"[警告] {where}:{tb}", flush=True)
    return True
