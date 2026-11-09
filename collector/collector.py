"""小红书采集端:轮询工作台领任务 → 用已登录的浏览器抓取 → 回传。协议见 docs/采集端接口.md。

    python collector/collector.py --login          # 首次:弹出浏览器,登录千帆后台,登录态存在 collector/profile
    python collector/collector.py                  # 正常运行(读取 collector/.env)
    python collector/collector.py --once --headless  # 领一个任务做完就退出

配置:collector/.env(见 .env.example)或同名命令行参数 / 环境变量。
"""
import argparse
import os
import re
import sys
import threading
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
import qianfan  # noqa: E402
import xhs  # noqa: E402

HERE = Path(__file__).parent
TYPES = os.environ.get("XHS_TYPES", "qianfan")   # 只领千帆任务;要同时采笔记可设 qianfan,keyword,title,author
FLUSH_EVERY = 5                  # 每采到几条先回传一批,图片链接有时效,尽早交给工作台转存


def load_env():
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def count_value(v):
    """'1.2万' / '3.4w' / '5k' / 123 → 数字,用于博主任务按点赞排序。"""
    if v in (None, ""):
        return 0
    s = str(v).strip().lower().replace(",", "")
    m = re.match(r"([\d.]+)\s*(万|w|千|k)?", s)
    if not m:
        return 0
    return int(float(m.group(1)) * {"万": 1e4, "w": 1e4, "千": 1e3, "k": 1e3}.get(m.group(2), 1))


class Collector:
    def __init__(self, a):
        self.a = a
        self.cli = httpx.Client(base_url=a.server, timeout=30, headers={"Authorization": f"Bearer {a.token}"})
        self.stop = threading.Event()
        self._reported = set()   # 已带着失败原因回传过的任务,避免重复上报

    # ---------- 工作台接口 ----------
    def ping(self, cli=None):
        r = (cli or self.cli).get("/api/ping", params={"worker": self.a.worker})
        r.raise_for_status()
        return r.json()

    def heartbeat(self):
        """采集一条笔记要好几秒,任务期间每 45 秒 ping 一次保持在线和续租。独立线程用独立连接。"""
        cli = httpx.Client(base_url=self.a.server, timeout=15, headers=self.cli.headers)
        while not self.stop.wait(45):
            try:
                self.ping(cli)
            except Exception as e:
                log("心跳失败:", e)

    def post(self, task_id, notes, done=True, error=""):
        notes = [{k: v for k, v in n.items() if v is not None} for n in notes]   # 页面上没有的字段不传
        r = self.cli.post("/api/notes", json={"task_id": task_id, "notes": notes, "done": done, "error": error})
        if r.status_code == 404:
            log(f"  任务 #{task_id} 已被用户删除,丢弃")
            return None
        r.raise_for_status()
        return r.json()

    def known_ids(self, ids):
        try:
            r = self.cli.post("/api/notes/known", params={"worker": self.a.worker}, json={"ids": ids[:500]})
            r.raise_for_status()
            return set(r.json().get("known", []))
        except Exception as e:
            log("  查询已有笔记失败,按全新处理:", e)
            return set()

    # ---------- 单个任务 ----------
    def run_qianfan(self, page, t):
        body = qianfan.run(page, t)
        body["task_id"] = t["id"]
        r = self.cli.post("/api/qianfan/ingest", json=body)
        if r.status_code == 400:
            raise xhs.CollectError(f"工作台拒收数据:{r.json().get('detail', r.text)}"[:200])
        r.raise_for_status()
        log("  回传:", r.json())

    def post_qf_error(self, task_id, kind, msg):
        self.cli.post("/api/qianfan/ingest", json={"task_id": task_id, "kind": kind, "error": msg})

    def run_task(self, page, t):
        if t["type"] == "qianfan":
            return self.run_qianfan(page, t)
        typ, limit = t["type"], max(1, int(t.get("limit") or 20))
        max_age = int(t.get("max_age_days") or 0)
        if typ == "author":
            cards = xhs.author_cards(page, t["query"], limit)
        elif typ in ("keyword", "title"):
            # 多取一些候选:有视频、过期、已存在的会被筛掉
            cards = xhs.search_cards(page, t["query"], limit * 3, t.get("sort", "popularity"), t.get("note_type", "image"))
        else:
            raise xhs.CollectError(f"本采集端不支持任务类型 {typ}")

        if t.get("skip_known") and typ != "author":
            known = self.known_ids([c["id"] for c in cards])
            cards = [c for c in cards if c["id"] not in known]
            log(f"  跳过工作台已有笔记 {len(known)} 条,剩 {len(cards)} 条待读")

        got, pending, fails = [], [], 0
        try:
            for c in cards:
                if typ != "author" and (c.get("video")):
                    continue                      # 关键词/标题任务不要视频
                if typ != "author" and len(got) >= limit:
                    break
                note = xhs.read_note(page, c)
                if not note:
                    fails += 1
                    log(f"  读不到 {c['id']} 的正文({fails})")
                    if fails >= 3 and not got:
                        raise xhs.CollectError("连续读不到笔记正文,可能被限流或页面改版,稍后重试")
                    continue
                fails = 0
                if typ != "author":
                    if note["type"] == "video":
                        continue
                    if xhs.too_old(note, max_age):
                        continue
                got.append(note)
                pending.append(note)
                log(f"  [{len(got)}] {note['title'][:24]}  赞{note['likes']} 藏{note['collects']} 评{note['comments']} 图{len(note['images'])}")
                if len(pending) >= FLUSH_EVERY and typ != "author":
                    self.post(t["id"], pending, done=False)
                    pending = []
        except xhs.CollectError as e:
            # 已抓到的照常入库,同时把原因报上去
            self.post(t["id"], got if typ == "author" else pending, error=str(e))
            self._reported.add(t["id"])
            raise
        if typ == "author":
            got.sort(key=lambda n: count_value(n["likes"]), reverse=True)
            pending = got[:limit]
        if not got:
            raise xhs.CollectError("没有采到符合条件的笔记(都是视频/已存在/超出时间范围)")
        res = self.post(t["id"], pending, done=True)
        log("  回传:", res)

    # ---------- 主循环 ----------
    def serve(self, ctx):
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        self.ping()
        threading.Thread(target=self.heartbeat, daemon=True).start()
        log(f"采集端 {self.a.worker} 已启动,轮询 {self.a.server}")
        while True:
            try:
                tasks = self.cli.get("/api/tasks", params={"limit": 1, "worker": self.a.worker, "types": TYPES}).raise_for_status().json()["tasks"]
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    raise SystemExit("Token 无效(401),请检查 .env")
                log("拉取任务失败:", e)
                tasks = []
            except Exception as e:
                log("拉取任务失败:", e)
                tasks = []
            for t in tasks:
                log(f"领到任务 #{t['id']} [{t['type']}] {t['query']} ×{t['limit']}")
                try:
                    self.run_task(page, t)
                except xhs.CollectError as e:
                    log("  失败:", e)
                    self._report_once(t["id"], str(e), t["query"] if t["type"] == "qianfan" else "")
                except Exception as e:
                    log("  异常:", repr(e))
                    self._report_once(t["id"], f"采集端异常:{e}"[:200], t["query"] if t["type"] == "qianfan" else "")
                if self.a.once:
                    return
                xhs.nap(4, 8)   # 任务之间歇一会儿,降低风控概率
            if not tasks:
                if self.a.once:
                    log("没有待领取的任务")
                    return
                time.sleep(self.a.interval)

    def _report_once(self, task_id, msg, kind=""):
        """run_task 里已经带着部分笔记上报过的任务不再重复报;其余失败在这里上报。"""
        if task_id in self._reported:
            return
        try:
            self.post(task_id, [], error=msg) if not kind else self.post_qf_error(task_id, kind, msg)
        except Exception as e:
            log("  上报失败原因时出错:", e)


def open_context(p, a):
    kw = dict(user_data_dir=str(HERE / "profile"), headless=a.headless, viewport={"width": 1366, "height": 860},
              locale="zh-CN", args=["--disable-blink-features=AutomationControlled"])
    try:
        return p.chromium.launch_persistent_context(channel=a.channel, **kw) if a.channel else p.chromium.launch_persistent_context(**kw)
    except Exception as e:
        log(f"启动浏览器({a.channel or '内置 Chromium'})失败:{e}")
        raise SystemExit("可用 --channel chrome / msedge 指定本机浏览器,或运行 playwright install chromium")


def qf_url():
    return qianfan.load_pages()["account"]["url"]


def qf_logged_in(page):
    return not any(w in page.url for w in ("login", "passport", "signin")) and page.url.startswith("http")


def do_login(a):
    with sync_playwright() as p:
        ctx = open_context(p, argparse.Namespace(**{**vars(a), "headless": False}))
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(qf_url(), wait_until="domcontentloaded")
        log("请在弹出的浏览器里登录千帆后台,进到账号数据页后会自动保存并退出(最多等 5 分钟)")
        for _ in range(150):
            time.sleep(2)
            try:
                if qf_logged_in(page) and "login" not in page.content()[:5000].lower():
                    time.sleep(5)
                    if qf_logged_in(page):
                        log("登录成功,登录态已保存到 collector/profile")
                        break
            except Exception:
                break
        else:
            log("等待超时,未检测到登录")
        ctx.close()


def do_probe(a):
    """不连工作台:打开页面并把接口 JSON 存到 collector/probe/,用来核对字段。"""
    with sync_playwright() as p:
        ctx = open_context(p, a)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            body = qianfan.run(page, {"query": a.probe, "params": {"period": "7d", "category": ""}}, probe=True)
            log("按当前别名解析的结果:", str(body)[:800])
        except xhs.CollectError as e:
            log("解析失败(原始数据已保存,可据此补字段别名):", e)
        ctx.close()


def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default=os.environ.get("XHS_SERVER", "http://127.0.0.1:18765"))
    ap.add_argument("--token", default=os.environ.get("XHS_TOKEN", ""))
    ap.add_argument("--worker", default=os.environ.get("XHS_WORKER", "local-pw"))
    ap.add_argument("--interval", type=float, default=float(os.environ.get("XHS_INTERVAL", 10)))
    ap.add_argument("--channel", default=os.environ.get("XHS_BROWSER", "chrome"), help="chrome / msedge / 空=内置 Chromium")
    ap.add_argument("--headless", action="store_true", default=os.environ.get("XHS_HEADLESS") == "1")
    ap.add_argument("--login", action="store_true", help="只做登录,保存登录态后退出")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--probe", default="", help="account/notes/hot_notes/hot_topics/hot_keywords:只抓页面接口数据存盘,不连工作台")
    a = ap.parse_args()
    if a.login:
        return do_login(a)
    if a.probe:
        return do_probe(a)
    if not a.token:
        raise SystemExit("缺少 Token:在 collector/.env 里填 XHS_TOKEN(管理端 → 接口配置 → 采集端对接)")
    c = Collector(a)
    try:
        log("自检通过:", c.ping())
    except Exception as e:
        raise SystemExit(f"自检失败,检查地址和 Token(docs/采集端接口.md 第 2 节):{e}")
    with sync_playwright() as p:
        ctx = open_context(p, a)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(qf_url(), wait_until="domcontentloaded")
            xhs.nap(2, 3)
            if not qf_logged_in(page):
                raise SystemExit("千帆未登录,先运行 login.bat 登录")
            c.serve(ctx)
        finally:
            c.stop.set()
            ctx.close()


if __name__ == "__main__":
    main()
