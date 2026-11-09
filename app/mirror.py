"""笔记图片转存:采集端回传的图片链接带时间戳签名、会过期,收到后立刻下载到本地,之后页面都用本地副本。
状态在 notes.mirror_status(pending / running / done / partial / error),服务重启后会接着跑。"""
import hashlib
import ipaddress
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

import httpx

from .db import DATA, conn, get_settings, row_to_dict

DIR = DATA / "note_images"
DIR.mkdir(exist_ok=True)
MAX_BYTES = 15 * 1024 * 1024
MEDIA = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif"}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
BENCHMARK = ipaddress.ip_network("198.18.0.0/15")  # 本机开了 fake-ip 代理时,域名会解析到这个段,属于正常公网访问
_wake = threading.Event()
_started = False


def wake():
    _wake.set()


# ---------- 下载 ----------
def check_url(url: str):
    """只允许 http(s) 且解析结果都是公网地址,防止借图片链接访问内网服务(SSRF)。"""
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("不是有效的图片链接")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError("域名解析失败")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global and ip not in BENCHMARK:
            raise ValueError("不允许访问内网或本机地址")


def sniff(b: bytes) -> str | None:
    """按文件头判断真实图片类型。不收 SVG(可内嵌脚本),也不信服务器返回的 Content-Type。"""
    if b[:3] == b"\xff\xd8\xff":
        return "jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    if b[4:12] in (b"ftypavif", b"ftypavis"):
        return "avif"
    return None


def name_for(url: str) -> str:
    """同一张图(域名+路径相同,忽略签名参数)只存一份。"""
    p = urlsplit(url)
    return hashlib.sha1(f"{p.netloc}{p.path}".encode()).hexdigest()[:24]


def fetch(client: httpx.Client, url: str) -> bytes:
    for _ in range(4):  # 手动跟随跳转,每一跳都重新检查地址
        check_url(url)
        try:
            with client.stream("GET", url, headers={"User-Agent": UA, "Referer": "https://www.xiaohongshu.com/"},
                               follow_redirects=False) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, r.headers.get("location", ""))
                    continue
                if r.status_code != 200:
                    raise IOError(f"HTTP {r.status_code}" + (",链接可能已过期" if r.status_code in (403, 410) else ""))
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > MAX_BYTES:
                        raise IOError("图片超过 15MB")
                return bytes(buf)
        except httpx.HTTPError as e:
            raise IOError(f"下载失败:{e.__class__.__name__}")
    raise IOError("重定向次数过多")


def download(client: httpx.Client, url: str) -> str:
    h = name_for(url)
    hit = next(iter(DIR.glob(h + ".*")), None)
    if hit:
        return hit.name
    data = fetch(client, url)
    ext = sniff(data)
    if not ext:
        raise IOError("返回的不是图片(链接可能已失效)")
    path = DIR / f"{h}.{ext}"
    tmp = DIR / f".{h}.{threading.get_ident()}.part"
    tmp.write_bytes(data)
    tmp.replace(path)
    return path.name


def _process(n: dict) -> tuple[str, list[str], str, str]:
    imgs = [u for u in (n["images"] or []) if isinstance(u, str)]
    cover = n["cover"] or ""
    urls = list(dict.fromkeys(u for u in [cover, *imgs] if u.startswith(("http://", "https://"))))
    if not urls:
        return "done", [], "", ""
    conc = max(1, min(8, int(get_settings().get("mirror_concurrency") or 4)))
    got, errs = {}, {}
    with httpx.Client(timeout=20) as client, ThreadPoolExecutor(conc) as ex:
        futs = {u: ex.submit(download, client, u) for u in urls}
        for u, f in futs.items():
            try:
                got[u] = f.result()
            except Exception as e:
                errs[u] = (str(e) or e.__class__.__name__)[:80]
    status = "done" if not errs else "partial" if got else "error"
    msg = f"{len(errs)}/{len(urls)} 张失败:" + ";".join(list(dict.fromkeys(errs.values()))[:2]) if errs else ""
    return status, [got.get(u, "") for u in imgs], got.get(cover, ""), msg


# ---------- 后台线程 ----------
def _loop():
    while True:
        _wake.wait(timeout=20)
        _wake.clear()
        try:
            while get_settings().get("mirror_images") == "1":
                with conn() as c:
                    r = c.execute("SELECT * FROM notes WHERE mirror_status='pending' ORDER BY id LIMIT 1").fetchone()
                    if not r:
                        break
                    c.execute("UPDATE notes SET mirror_status='running' WHERE id=?", (r["id"],))
                try:
                    status, local, cover_local, msg = _process(row_to_dict(r))
                except Exception as e:
                    status, local, cover_local, msg = "error", [], "", str(e)[:200]
                with conn() as c:
                    c.execute("UPDATE notes SET mirror_status=?, images_local=?, cover_local=?, mirror_error=? WHERE id=?",
                              (status, json.dumps(local), cover_local, msg, r["id"]))
        except Exception:
            time.sleep(5)


def start():
    global _started
    if not _started:
        _started = True
        threading.Thread(target=_loop, daemon=True, name="mirror").start()
    wake()


# ---------- 维护 ----------
def retry(ids: list[int] | None = None) -> int:
    """把转存失败(error / partial)的笔记重新排队;传 ids 则只重试这些。"""
    with conn() as c:
        if ids:
            n = sum(c.execute("UPDATE notes SET mirror_status='pending', mirror_error='' WHERE id=? AND mirror_status!='running'",
                              (i,)).rowcount for i in ids)
        else:
            n = c.execute("UPDATE notes SET mirror_status='pending', mirror_error='' WHERE mirror_status IN ('error','partial')").rowcount
    wake()
    return n


def gc() -> int:
    """删除不再被任何笔记引用的图片文件。10 分钟内新写入的不动,避免和正在转存的任务冲突。"""
    keep = set()
    with conn() as c:
        for r in c.execute("SELECT images_local, cover_local FROM notes"):
            keep.update(json.loads(r["images_local"] or "[]"))
            keep.add(r["cover_local"])
    n = 0
    for f in DIR.iterdir():
        if f.is_file() and not f.name.startswith(".") and f.name not in keep and time.time() - f.stat().st_mtime > 600:
            f.unlink()
            n += 1
    return n


def disk_mb() -> float:
    return round(sum(f.stat().st_size for f in DIR.iterdir() if f.is_file()) / 1048576, 1)
