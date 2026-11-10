"""Live 图「真实动作」的视频 worker:在装了 ComfyUI(MiniMax H3 图生视频)的电脑上跑。
从工作台领任务 → 把静图裁成 3:4 → 交给本机 ComfyUI 生成 3 秒左右「手机随手拍」风格的视频 → 回传工作台。
之后由 Mac 端(mac_worker.py)以这条视频为源做成实况 .pvt。

    python video_worker.py --server http://127.0.0.1:18765 --token 采集端Token

常用参数:--comfy ComfyUI 地址(默认 http://127.0.0.1:8188)  --name 这台电脑的名字  --once 只做一个就退出
          --size 864x1152 输出尺寸(宽x高,必须是 3:4,越大越清楚越慢)  --steps 4 采样步数(配 4 步加速 LoRA)
只依赖 Pillow;图片适配用同目录的 imgfit.py。ComfyUI 需要装好 MiniMax H3 模型和 Director 节点(见下面 MODELS)。"""
from __future__ import annotations

import argparse
import io
import json
import random
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from PIL import Image  # noqa: E402
from imgfit import fit_to_3x4  # noqa: E402

MODELS = {   # ComfyUI 里的模型文件名(models/diffusion_models、loras、text_encoders、vae)
    "unet": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    "lora": "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
    "clip": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "video_vae": "minimax_h3_video_vae_fp16.safetensors",
    "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
}
WAIT_MAX = 25 * 60   # 一条视频最多等多少秒;超过就中断,避免卡死显卡


class Http:
    def __init__(self, base: str, headers: dict | None = None):
        self.base, self.headers = base.rstrip("/"), headers or {}

    def call(self, method: str, path: str, body: bytes | None = None, ctype: str = "application/json", timeout: int = 60) -> bytes:
        req = urllib.request.Request(self.base + path, data=body, method=method, headers={**self.headers, "Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}:{e.read().decode('utf-8', 'replace')[:300]}") from None

    def json(self, method: str, path: str, obj=None, timeout: int = 60):
        return json.loads(self.call(method, path, json.dumps(obj).encode() if obj is not None else None, timeout=timeout))


# ---------------- ComfyUI ----------------
def comfy_upload(comfy: Http, img: Image.Image, name: str) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    b = uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\nContent-Type: image/png\r\n\r\n").encode() + buf.getvalue() \
        + (f"\r\n--{b}\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\ninput\r\n--{b}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{b}--\r\n").encode()
    return json.loads(comfy.call("POST", "/upload/image", body, f"multipart/form-data; boundary={b}", timeout=120))["name"]


def build_graph(image_name: str, prompt: str, seconds: float, w: int, h: int, steps: int, i2v_task: str, seed: int) -> dict:
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": MODELS["unet"], "weight_dtype": "default"}},
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": MODELS["lora"], "strength_model": 1.0}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": MODELS["clip"], "type": "minimax", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": MODELS["video_vae"]}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": MODELS["audio_vae"]}},
        "6": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "7": {"class_type": "MiniMaxH3DirectorGroupImageToVideo", "inputs": {"prompt": prompt, "duration_sec": seconds, "first_frame": ["6", 0]}},
        "8": {"class_type": "MiniMaxH3Director", "inputs": {
            "model": ["2", 0], "video_vae": ["4", 0], "audio_vae": ["5", 0], "clip": ["3", 0], "task_type": i2v_task,
            "global_prompt": "", "cfg": 1.0, "seed": seed, "frame_rate": 24.0, "width": w, "height": h, "ref_max_size": h,
            "total_frames": int(seconds * 24) + 1, "bd_grp_sample": "基础设置", "timeline_data": "", "i2v_groups": ["7", 0], "steps": steps}},
        "9": {"class_type": "CreateVideo", "inputs": {"images": ["8", 0], "audio": ["8", 1], "fps": ["8", 2]}},
        "10": {"class_type": "SaveVideo", "inputs": {"video": ["9", 0], "filename_prefix": "video/xhs_live", "format": "mp4", "codec": "h264"}},
    }


def comfy_video(comfy: Http, img: Image.Image, job_id: int, prompt: str, seconds: float, w: int, h: int, steps: int) -> bytes:
    try:
        info = comfy.json("GET", "/object_info/MiniMaxH3Director", timeout=20)
    except Exception as e:
        raise RuntimeError(f"连不上本机 ComfyUI({comfy.base}):{e}。先启动 ComfyUI,并确认装了 MiniMax H3 Director 节点") from None
    task = next(t for t in info["MiniMaxH3Director"]["input"]["required"]["task_type"][0] if t.startswith("i2v"))
    name = comfy_upload(comfy, img, f"xhs_live_{job_id}.png")
    pid = comfy.json("POST", "/prompt", {"prompt": build_graph(name, prompt, seconds, w, h, steps, task, random.randint(0, 2 ** 31)), "client_id": uuid.uuid4().hex})["prompt_id"]
    t0 = time.time()
    while True:
        time.sleep(3)
        h_ = comfy.json("GET", f"/history/{pid}", timeout=30)
        if pid in h_:
            st = h_[pid]["status"]
            if st["status_str"] != "success":
                msgs = json.dumps(st.get("messages"), ensure_ascii=False)
                raise RuntimeError("ComfyUI 生成失败:" + msgs[-300:])
            for o in h_[pid]["outputs"].values():
                for f in o.get("images", []) + o.get("videos", []) + o.get("gifs", []):
                    if f["filename"].lower().endswith((".mp4", ".mov")):
                        q = urllib.parse.urlencode({"filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")})
                        return comfy.call("GET", f"/view?{q}", timeout=180)
            raise RuntimeError("ComfyUI 跑完了但没有找到视频输出")
        if time.time() - t0 > WAIT_MAX:
            try:
                comfy.call("POST", "/interrupt", b"{}")
            finally:
                raise RuntimeError(f"等了 {WAIT_MAX // 60} 分钟还没出视频,已中断(显卡可能被别的任务占满了)")


# ---------------- 工作台 ----------------
def run_job(api: Http, comfy: Http, name: str, job: dict, w: int, h: int, steps: int):
    t0 = time.time()
    print(f"[任务 {job['id']}] 动作「{job.get('motion')}」 {job['seconds']} 秒", flush=True)
    src = Image.open(io.BytesIO(api.call("GET", job["source"] + f"?worker={urllib.parse.quote(name)}", timeout=120)))
    fo = job["opts"]
    img = fit_to_3x4(src, fo.get("fit", "crop"), fo.get("focus_y", 0.5)).resize((w, h), Image.LANCZOS)
    video = comfy_video(comfy, img, job["id"], job["prompt"], float(job["seconds"]), w, h, steps)
    api.call("PUT", f"/api/live/vworker/jobs/{job['id']}/video?worker={urllib.parse.quote(name)}", video, "video/mp4", timeout=300)
    print(f"   完成 {time.time() - t0:.0f} 秒,视频 {len(video) / 1048576:.1f} MB,已交给 Mac 队列", flush=True)


def main():
    ap = argparse.ArgumentParser(description="Live 图 视频 worker(ComfyUI + MiniMax H3)")
    ap.add_argument("--server", required=True, help="工作台地址,如 http://127.0.0.1:18765")
    ap.add_argument("--token", required=True, help="采集端 Token(管理端「接口配置」里)")
    ap.add_argument("--comfy", default="http://127.0.0.1:8188", help="本机 ComfyUI 地址")
    ap.add_argument("--name", default=socket.gethostname().split(".")[0], help="这台电脑的名字(工作台上显示)")
    ap.add_argument("--size", default="864x1152", help="输出尺寸 宽x高(3:4)。包装上的小字要清晰,至少 768x1024;864x1152 约 70 秒/条,960x1280 约 85 秒/条,576x768 字会糊")
    ap.add_argument("--steps", type=int, default=4)
    ap.add_argument("--interval", type=int, default=5)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    w, h = (int(x) for x in a.size.lower().split("x"))
    if abs(w / h - 0.75) > 0.01:
        raise SystemExit("--size 必须是 3:4,例如 576x768、768x1024")
    api = Http(a.server, {"Authorization": f"Bearer {a.token}"})
    comfy = Http(a.comfy)
    q = f"?worker={urllib.parse.quote(a.name)}"
    try:
        info = api.json("GET", "/api/live/vworker/ping" + q)
    except Exception as e:
        raise SystemExit(f"连不上工作台({a.server}):{e}\n检查:地址、端口、Token(管理端「接口配置」里的采集端 Token)")
    print(f"已连接工作台,待生成视频 {info['pending']} 个。本机 ComfyUI:{a.comfy}。Ctrl+C 退出。", flush=True)
    idle = False
    try:
        while True:
            try:
                job = api.json("GET", "/api/live/vworker/claim" + q).get("job")
            except Exception as e:
                print(f"领任务失败:{e},{a.interval * 3} 秒后重试", flush=True)
                time.sleep(a.interval * 3)
                continue
            if not job:
                if a.once:
                    print("没有待生成的视频")
                    return
                if not idle:
                    print("暂时没有任务,等待中…", flush=True)
                    idle = True
                time.sleep(a.interval)
                continue
            idle = False
            try:
                run_job(api, comfy, a.name, job, w, h, a.steps)
            except Exception as e:
                print(f"   失败:{e}", flush=True)
                try:
                    api.json("POST", f"/api/live/vworker/jobs/{job['id']}/fail{q}", {"error": str(e)[:480]})
                except Exception as e2:
                    print(f"   (没能把失败原因告诉工作台:{e2})", flush=True)
            if a.once:
                return
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()
