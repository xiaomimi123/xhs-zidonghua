"""生图。按 OpenAI 兼容的 POST {base}/images/generations 调用,自动识别两种返回:
  - 同步:直接返回 data[0].b64_json 或 data[0].url
  - 异步(如 apimart):返回 task_id,轮询 GET {base}/tasks/{task_id},完成后从 result.images[0].url 取图
下载后按文件头判断真实格式再保存。换用格式不同的服务商时,只需要改这个文件。"""
import base64
import random
import re
import shutil
import threading
import time
from pathlib import Path

import httpx

from . import mirror, thumbs
from .db import get_settings

IMG_DIR = Path(__file__).resolve().parent.parent / "data" / "images"
IMG_DIR.mkdir(parents=True, exist_ok=True)
POLL_EVERY, POLL_MAX = 3, 240  # 异步任务:每 3 秒查一次,最多等 4 分钟(正常 30~60 秒出图;超时不自动重提,免得服务商那边其实已出图又重复扣费,可点「补全缺失的图」)
DOWNLOAD_WAITS = (0, 3, 6, 10, 15)  # 图片下载:只重试下载本身
RETRY_WAITS = (0, 4, 10, 25)  # 网络抖动、429、5xx 这类瞬时错误:最多试 4 次,间隔约 4、10、25 秒(带随机抖动,避免一批请求同时重试);429 带 Retry-After 时按它等


class Transient(RuntimeError):
    """可以重试的错误(连不上、限流、服务商暂时不可用)。余额不足、Key 错误、内容审核不通过不重试。
    retry_after:服务商要求等多少秒再试(429 响应头 Retry-After),没有就是 0。"""
    retry_after = 0


class _Gate:
    """所有生图请求共用的并发闸门:不管是复刻稿子的配图还是 Live 的场景图,同一时间向生图服务商发出的请求不超过设置的数量。
    数量取自设置 image_api_concurrency,等待期间设置改了会跟着变。"""

    def __init__(self):
        self.cv, self.n, self.peak = threading.Condition(), 0, 0

    @staticmethod
    def limit() -> int:
        try:
            return max(1, min(16, int(get_settings().get("image_api_concurrency") or 4)))
        except (TypeError, ValueError):
            return 4

    def acquire(self):
        with self.cv:
            while self.n >= self.limit():
                self.cv.wait(timeout=2)
            self.n += 1
            self.peak = max(self.peak, self.n)

    def release(self):
        with self.cv:
            self.n -= 1
            self.cv.notify()


GATE = _Gate()
HINTS = {401: "API Key 不正确或已失效", 402: "账户余额不足", 403: "这个 Key 没有权限使用该模型", 404: "接口地址或路径不对",
         429: "请求太频繁,稍后再试"}


def _explain(r: httpx.Response) -> RuntimeError:
    try:
        j = r.json()
        e = j.get("error") if isinstance(j, dict) else None
        msg = (e.get("message") if isinstance(e, dict) else e) or (j.get("message") if isinstance(j, dict) else "") or ""
    except ValueError:
        msg = r.text[:150]
    hint = HINTS.get(r.status_code)
    cls = Transient if r.status_code == 429 or r.status_code >= 500 else RuntimeError
    e = cls(f"生图接口返回 {r.status_code}" + (f":{hint}" if hint else "") + (f"({str(msg)[:140]})" if msg else ""))
    if cls is Transient:
        try:
            e.retry_after = min(60, max(0, int(float(r.headers.get("retry-after", "0")))))
        except (TypeError, ValueError):
            pass
    return e


def _first(j) -> dict:
    """兼容 {"data": [...]} / {"data": {...}} / 直接返回对象 三种包装。"""
    d = j.get("data", j) if isinstance(j, dict) else j
    if isinstance(d, list):
        d = d[0] if d else {}
    return d if isinstance(d, dict) else {}


def _result_url(task: dict) -> str:
    for im in (task.get("result") or {}).get("images") or []:
        u = im.get("url") if isinstance(im, dict) else im
        if isinstance(u, list):
            u = u[0] if u else ""
        if u:
            return str(u)
    return ""


def _poll(c: httpx.Client, base: str, task_id: str) -> str:
    deadline, errors = time.time() + POLL_MAX, 0
    while time.time() < deadline:
        # 查询状态时的网络抖动不能重新提交任务(任务可能已经在服务商那边出图,重提会重复扣费),只在原任务上继续查
        try:
            r = c.get(f"{base}/tasks/{task_id}", params={"language": "zh"})
            bad = r.status_code >= 500 or r.status_code == 429
        except httpx.HTTPError:
            r, bad = None, True
        if bad:
            errors += 1
            if errors >= 6:
                raise RuntimeError(f"查询生图任务状态连续失败(任务号 {task_id},服务商那边可能已经出图,可到服务商后台查看)")
            time.sleep(POLL_EVERY)
            continue
        errors = 0
        if r.status_code >= 400:
            raise _explain(r)
        t = _first(r.json())
        status = str(t.get("status", "")).lower()
        if status == "completed":
            url = _result_url(t)
            if not url:
                raise RuntimeError("生图任务已完成,但没有返回图片地址")
            return url
        if status in ("failed", "cancelled"):
            e = t.get("error") or {}
            raise RuntimeError("生图失败:" + str((e.get("message") if isinstance(e, dict) else e) or status)[:160])
        time.sleep(POLL_EVERY)
    raise RuntimeError(f"生图超时:等了 {POLL_MAX // 60} 分钟还没出图")


def generate_image(prompt: str, name: str, s: dict | None = None, refs: list | None = None) -> str:
    """生成一张图并保存到 data/images,返回文件名。瞬时错误自动重试。
    s 可传入临时配置(测试用),不传则读已保存的设置。"""
    err, hint = None, 0
    for wait in RETRY_WAITS:
        if wait:   # 等待期间不占并发名额;带随机抖动,429 按服务商要求的时间等
            time.sleep(max(hint, wait * (0.75 + random.random() * 0.5)))
        GATE.acquire()
        try:
            return _generate_once(prompt, name, s, refs)
        except Transient as e:
            err, hint = e, e.retry_after
        finally:
            GATE.release()
    raise RuntimeError(f"{err}(已自动重试 {len(RETRY_WAITS)} 次)")


def _generate_once(prompt: str, name: str, s: dict | None = None, refs: list | None = None) -> str:
    s = s or get_settings()
    base, key = (s.get("image_base_url") or "").rstrip("/"), s.get("image_api_key") or ""
    if not (base and key):
        raise RuntimeError("请先在管理端「接口配置」里填写生图接口的地址和 Key")
    payload = {"prompt": prompt, "n": 1}
    for field, k in (("model", "image_model"), ("size", "image_size"), ("resolution", "image_resolution")):
        if s.get(k):
            payload[field] = s[k]  # 没填的不传,让服务商用默认值
    if refs:  # 参考图:让模型保持产品外观不变,只换场景(服务商的 image_urls 参数,支持 data URL)
        urls = []
        for f in refs:
            b = open(f, "rb").read()
            urls.append(f"data:image/{'jpeg' if mirror.sniff(b) == 'jpg' else mirror.sniff(b) or 'png'};base64,{base64.b64encode(b).decode()}")
        payload["image_urls"] = urls
    with httpx.Client(timeout=120 if refs else 60, headers={"Authorization": f"Bearer {key}"}) as c:
        try:
            r = c.post(base + "/images/generations", json=payload)
            if r.status_code >= 400:
                raise _explain(r)
            item = _first(r.json())
            url = item.get("url") or ""
            if not item.get("b64_json") and not url and item.get("task_id"):
                url = _poll(c, base, str(item["task_id"]))
        except httpx.HTTPError as e:
            raise Transient(f"连不上生图接口:{e.__class__.__name__}")
    if item.get("b64_json"):
        data = base64.b64decode(item["b64_json"])
    elif url:
        with httpx.Client(timeout=60) as dl:  # 下载用单独的客户端,不能把 API Key 发给图片服务器
            # 图已经生成好了,下载失败只重试下载;不能整体重新提交,否则会重复生成、重复扣费
            for i, wait in enumerate(DOWNLOAD_WAITS):
                if wait:
                    time.sleep(wait)
                try:
                    data = mirror.fetch(dl, url)
                    break
                except (OSError, ValueError) as e:  # 下载时的网络抖动、域名解析失败可以重试
                    if not str(e).startswith(("下载失败", "域名解析失败")):
                        raise
                    if i == len(DOWNLOAD_WAITS) - 1:
                        raise Transient(f"{e}(图片已生成,但下载失败,任务号见服务商后台)")
    else:
        raise RuntimeError("生图接口既没有返回图片也没有返回任务号:" + str(item)[:150])
    ext = mirror.sniff(data)
    if not ext:
        raise RuntimeError("生图接口返回的不是图片")
    path = IMG_DIR / f"{name}.{ext}"
    path.write_bytes(data)
    thumbs.warm(path)   # 顺手做好列表用的缩略图,第一次打开成品库就不用等
    return path.name


# ---------- 管理端「测试」 ----------
def check(s: dict) -> dict:
    """免费检查:服务器能不能连上、Key 对不对(不生图、不扣费)。"""
    base, key = (s.get("image_base_url") or "").rstrip("/"), s.get("image_api_key") or ""
    if not (base and key):
        return {"ok": False, "msg": "请先填写接口地址和 API Key"}
    try:
        r = httpx.get(base + "/models", headers={"Authorization": f"Bearer {key}"}, timeout=15)
    except httpx.HTTPError as e:
        return {"ok": False, "msg": f"连不上 {base}:{e.__class__.__name__}。检查地址是否正确、服务器能否访问外网"}
    if r.status_code == 200:
        try:
            names = [m.get("id") for m in r.json().get("data", []) if isinstance(m, dict)]
        except ValueError:
            names = []
        model = s.get("image_model") or ""
        extra = "" if not (names and model) else (f";模型列表里有 {model}" if model in names else f";但模型列表里没找到 {model},请核对模型名")
        return {"ok": True, "msg": "连接正常,Key 有效" + extra}
    if r.status_code in (401, 403):
        return {"ok": False, "msg": "服务器连得上,但 Key 不正确或没有权限"}
    if r.status_code in (404, 405):
        return {"ok": None, "msg": "服务器连得上,但这个服务商没有「模型列表」接口,没法免费检查 Key。请点「试生成一张图」(会按 1 张图扣费)"}
    return {"ok": False, "msg": f"服务器返回 {r.status_code}"}


def probe(s: dict) -> dict:
    """真实生成一张图,验证地址、Key、模型名、尺寸参数和余额都没问题。会扣 1 张图的费用。"""
    for f in IMG_DIR.glob("_probe.*"):
        f.unlink()
    t0 = time.time()
    name = generate_image("一杯拿铁咖啡的竖版海报,极简风格,大面积留白,画面中不要出现任何文字", "_probe", s)
    return {"file": name, "seconds": round(time.time() - t0, 1)}


def target_ratio(setting: str | None = None) -> float:
    """生图比例设置(如 3:4 或 1024x1536)→ 宽/高;认不出来按小红书的 3:4。"""
    v = str(setting if setting is not None else get_settings().get("image_size") or "")
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*[:xX*]\s*(\d+(?:\.\d+)?)\s*$", v)
    return float(m.group(1)) / float(m.group(2)) if m and float(m.group(2)) else 0.75


def fit_ratio(src, dst, ratio: float | None = None) -> None:
    """把图片居中裁成目标比例存到 dst(略偏上取景,产品通常在画面中上部)。比例已经接近(误差 3% 内)、或图片读不了,就原样复制。"""
    ratio = ratio or target_ratio()
    try:
        from PIL import Image
        with Image.open(src) as im:
            w, h = im.size
            if abs(w / h - ratio) / ratio <= 0.03:
                raise ValueError("ratio ok")
            if w / h > ratio:      # 太宽:裁左右
                nw = int(h * ratio)
                box = ((w - nw) // 2, 0, (w - nw) // 2 + nw, h)
            else:                  # 太高:裁上下,略偏上
                nh = int(w / ratio)
                top = int((h - nh) * 0.4)
                box = (0, top, w, top + nh)
            out = im.crop(box)
            out.load()   # 出了 with 之后原图就关了,要先把裁出来的像素读进内存
            if str(dst).lower().endswith((".jpg", ".jpeg")) and out.mode != "RGB":
                out = out.convert("RGB")
            out.save(dst)
    except Exception:
        shutil.copy2(src, dst)
