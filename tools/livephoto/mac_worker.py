"""Live 图 Mac 端:从工作台领任务 → 渲染推近动画 → 用 makelive 打成 .pvt 包 → 回传工作台。

在 MacBook 上跑(AirDrop 把 .pvt 传到 iPhone,「照片」里就是一张会动的 Live 图):
    pip3 install -r requirements-mac.txt
    python3 mac_worker.py --server http://工作台地址:18765 --token 采集端Token

不连工作台、只想把一个文件夹里的图批量做成 Live 图:
    python3 mac_worker.py --local ~/Pictures/待做 --out ~/LivePhotos

常用参数:--name 这台电脑的名字(工作台上显示)  --out 成品保存目录(默认 ~/LivePhotos)
          --once 领完当前任务就退出  --no-pvt 不调用 makelive(只在没有 macOS 时测试流程用,做出来的包 iPhone 认不了)
          --import-photos 每做完一个包就自动导入本机「照片」App,相册里立刻是实况图(成品同时回传工作台)
只用标准库发请求;图片处理和视频编码复用同目录的 make_live.py。"""
from __future__ import annotations

import argparse
import io
import json
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_live  # noqa: E402

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp"}


# ---------------- 一张图 → .pvt 包 ----------------
def make_pvt(src: Path, out: Path, slug: str, opts: dict, use_makelive: bool = True, video: Path | None = None, keep_audio: bool = False, sharpen: float = 0.6) -> Path:
    """渲染 JPG + MOV,再打成 out/IMG_{slug}_LIVE.pvt(文件夹,双击导入「照片」)。
    video 有值 = 真实动作模式:以这条视频为源(src 是关键帧原图);没有 = 慢推镜头动画。"""
    with tempfile.TemporaryDirectory() as td:
        if video:
            jpg, mov, asset_id = make_live.build_pair_from_video(video, src, Path(td), slug, opts.get("fit", "crop"), opts.get("focus_y", 0.5), keep_audio, sharpen)
        else:
            jpg, mov, asset_id = make_live.build_pair(src, Path(td), slug, opts.get("seconds", 5), 30, opts.get("zoom", 1.08),
                                                      opts.get("focus_y", 0.35), opts.get("fit", "crop"))
        out.mkdir(parents=True, exist_ok=True)
        pvt = out / f"{jpg.stem}.pvt"
        if pvt.exists():
            shutil.rmtree(pvt)
        if use_makelive:
            try:
                from makelive import save_live_photo_pair_as_pvt
            except ImportError as e:
                raise RuntimeError("没有安装 makelive(只能在 macOS 上用):pip3 install makelive") from e
            _, p = save_live_photo_pair_as_pvt(jpg, mov, out)
            pvt = Path(p)
        else:   # 没有 macOS 时的测试流程:只把两个文件放进文件夹,没有 makelive 加的系统级信息
            pvt.mkdir()
            shutil.copy(jpg, pvt)
            shutil.copy(mov, pvt)
            (pvt / "metadata.plist").write_text("<plist><!-- 未经 makelive 处理,仅用于流程测试 --></plist>", encoding="utf-8")
    return pvt


def zip_dir(d: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:   # MOV 已经压缩过,不再压
        for f in sorted(d.rglob("*")):
            if f.is_file():
                z.write(f, f"{d.name}/{f.relative_to(d).as_posix()}")
    return buf.getvalue()


# ---------------- 工作台接口 ----------------
class Api:
    def __init__(self, server: str, token: str, name: str):
        self.base, self.token, self.name = server.rstrip("/"), token, name

    def call(self, method: str, path: str, body: bytes | None = None, ctype: str = "application/json", timeout: int = 60) -> bytes:
        sep = "&" if "?" in path else "?"
        req = urllib.request.Request(f"{self.base}{path}{sep}worker={urllib.parse.quote(self.name)}", data=body, method=method,
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:200]
            raise RuntimeError(f"HTTP {e.code}:{msg}") from None

    def ping(self):
        return json.loads(self.call("GET", "/api/live/worker/ping"))

    def claim(self):
        return json.loads(self.call("GET", "/api/live/worker/claim")).get("job")

    def source(self, job) -> bytes:
        return self.call("GET", job["source"].split("?")[0], timeout=120)

    def upload(self, job_id: int, data: bytes):
        self.call("PUT", f"/api/live/worker/jobs/{job_id}/result", data, "application/zip", timeout=300)

    def fail(self, job_id: int, error: str):
        try:
            self.call("POST", f"/api/live/worker/jobs/{job_id}/fail", json.dumps({"error": error}).encode())
        except Exception as e:   # 回报失败本身失败了:工作台会在超时后自动退回任务,这里只记一下
            print(f"   (没能把失败原因告诉工作台:{e})")


def import_to_photos(pvt: Path, album: str = ""):
    """把 .pvt 包导入本机「照片」App(等效于在 Finder 里双击)。导入后相册里就是一张实况图。
    album 有值时,把刚导入的项归入以它命名的相册(按任务发起人分类,没有就放「未署名」)。"""
    try:
        subprocess.run(["open", "-a", "Photos", str(pvt)], check=True, timeout=60)
        print(f"   已导入照片 App:{pvt.name}" + (f" → 相册「{album}」" if album else ""))
        time.sleep(5)   # 给照片 App 一点导入时间
        if album:
            _assign_album(pvt.name.replace(".pvt", ""), album)
    except Exception as e:
        print(f"   导入照片 App 失败(不影响回传):{e}")


def _assign_album(stem: str, album: str):
    """把照片 App 里文件名匹配的最新导入项归入指定相册。相册不存在就建。"""
    safe_stem = stem.replace('"', '')
    safe_album = album.replace('"', '') or "未署名"
    script = f'''
    tell application "Photos"
        set matches to every media item whose filename starts with "{safe_stem}"
        if (count of matches) is 0 then return "no-match"
        set mi to last item of matches   -- 匹配项里最后一个 = 最新导入的
        if (count of (every album whose name is "{safe_album}")) is 0 then
            make new album named "{safe_album}"
        end if
        add mi to album "{safe_album}"
        return "ok"
    end tell'''
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=60)
        out = (r.stdout or "").strip()
        if r.returncode == 0 and out == "ok":
            return
        print(f"   相册归类失败(图还在「最近项目」里,不影响回传):{(r.stderr or out or '未知原因').strip()[:120]}")
    except Exception as e:
        print(f"   相册归类失败(图还在「最近项目」里,不影响回传):{e}")


def run_job(api: Api, job: dict, out: Path, use_makelive: bool, keep_audio: bool = False, first_frame_still: bool = False, sharpen: float = 0.6, to_photos: bool = False):
    t0 = time.time()
    print(f"[任务 {job['id']}] {job.get('name', '')}  {'真实动作(以视频为源)' if job.get('video') else '慢推镜头'}  参数 {job['opts']}")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / (Path(job["image"]).name or "src.png")
        src.write_bytes(api.source(job))
        video = None
        if job.get("video"):
            video = Path(td) / "motion.mp4"
            video.write_bytes(api.call("GET", job["video"].split("?")[0], timeout=180))
        # 真实动作:关键照片默认用原图(和视频用同一个裁切,包装上的字清晰);--first-frame-still 改取视频第一帧(和视频画面完全一致,但视频分辨率低、字会糊)
        pvt = make_pvt(None if (first_frame_still and video) else src, out, f"L{job['id']}", job["opts"], use_makelive, video, keep_audio, sharpen)
    if to_photos:
        import_to_photos(pvt, job.get("by") or "")   # 按任务发起人分相册
    api.upload(job["id"], zip_dir(pvt))
    print(f"   完成 {time.time() - t0:.0f} 秒 → {pvt}")


def serve(a):
    api = Api(a.server, a.token, a.name)
    try:
        info = api.ping()
    except Exception as e:
        raise SystemExit(f"连不上工作台({a.server}):{e}\n检查:地址是不是电脑的局域网 IP(不是 127.0.0.1)、端口、Token(管理端「接口配置」里的采集端 Token)")
    print(f"已连接工作台,待做 {info['pending']} 个。Ctrl+C 退出。成品保存在 {a.out}")
    idle_logged = False
    while True:
        try:
            job = api.claim()
        except Exception as e:
            print(f"领任务失败:{e},{a.interval * 3} 秒后重试")
            time.sleep(a.interval * 3)
            continue
        if not job:
            if a.once:
                print("没有待做的任务")
                return
            if not idle_logged:
                print("暂时没有任务,等待中…")
                idle_logged = True
            time.sleep(a.interval)
            continue
        idle_logged = False
        try:
            run_job(api, job, a.out, not a.no_pvt, a.keep_audio, a.first_frame_still, a.sharpen, a.import_photos)
        except Exception as e:
            print(f"   失败:{e}")
            api.fail(job["id"], f"{type(e).__name__}: {e}")
        if a.once:
            return


def local(a):
    src = Path(a.local).expanduser()
    files = sorted(p for p in (src.iterdir() if src.is_dir() else [src]) if p.suffix.lower() in IMG_EXT)
    if not files:
        raise SystemExit(f"{src} 里没有图片({', '.join(sorted(IMG_EXT))})")
    opts = {"seconds": a.seconds, "zoom": a.zoom, "focus_y": a.focus_y, "fit": a.fit}
    ok = 0
    for i, f in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {f.name}")
        try:
            pvt = make_pvt(f, a.out, f"{f.stem}", opts, not a.no_pvt)
            if a.import_photos:
                import_to_photos(pvt)
            print(f"   → {pvt}")
            ok += 1
        except Exception as e:
            print(f"   失败:{e}")
    print(f"完成 {ok}/{len(files)},成品在 {a.out}(.pvt 双击导入「照片」,或 AirDrop 到 iPhone)")


def main():
    ap = argparse.ArgumentParser(description="Live 图 Mac 端")
    ap.add_argument("--server", help="工作台地址,如 http://192.168.1.100:18765")
    ap.add_argument("--token", help="采集端 Token(管理端「接口配置」里复制)")
    ap.add_argument("--local", metavar="目录或图片", help="本地模式:不连工作台,把这个目录(或单张图)做成 Live 图")
    ap.add_argument("--name", default=socket.gethostname().split(".")[0], help="这台电脑的名字(工作台上显示)")
    ap.add_argument("--out", type=lambda s: Path(s).expanduser(), default=Path("~/LivePhotos").expanduser(), help="成品保存目录")
    ap.add_argument("--interval", type=int, default=5, help="没有任务时每隔几秒问一次")
    ap.add_argument("--once", action="store_true", help="只做一个任务就退出")
    ap.add_argument("--no-pvt", action="store_true", help="不调用 makelive(非 macOS 测试用)")
    ap.add_argument("--import-photos", action="store_true", help="每做完一个包就自动导入本机「照片」App(成品同时回传工作台)")
    ap.add_argument("--keep-audio", action="store_true", help="真实动作模式:保留视频里模型生成的声音(默认去掉)")
    ap.add_argument("--sharpen", type=float, default=0.6, help="真实动作模式:视频轻锐化强度(AI 视频偏软),0 = 关闭,超过 1 容易出白边")
    ap.add_argument("--first-frame-still", action="store_true", help="真实动作模式:关键照片取视频第一帧,而不是原图")
    ap.add_argument("--seconds", type=float, default=5)
    ap.add_argument("--zoom", type=float, default=1.08)
    ap.add_argument("--focus-y", type=float, default=0.35)
    ap.add_argument("--fit", choices=("crop", "blur"), default="crop")
    a = ap.parse_args()
    if platform.system() != "Darwin" and not a.no_pvt:
        raise SystemExit("makelive 只能在 macOS 上用。想在别的系统上测试流程,加 --no-pvt(做出的包 iPhone 认不了)。")
    if a.local:
        return local(a)
    if not (a.server and a.token):
        ap.error("要么 --server 和 --token 一起给,要么用 --local 本地模式")
    try:
        serve(a)
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()
