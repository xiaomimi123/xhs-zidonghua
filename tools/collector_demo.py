"""采集端参考实现:轮询工作台领任务 → 抓取 → 回传。

真实采集请把 fetch_notes() 换成你自己的小红书抓取逻辑;
不想抓、只想联调工作台时加 --mock,会生成假笔记回传。

    python tools/collector_demo.py --mock --token <管理端「接口配置」里的 Token>
    python tools/collector_demo.py --server http://127.0.0.1:18765 --token xxx --worker pc-01
"""
import argparse
import random
import time
import httpx


def fetch_notes(task: dict) -> list[dict]:
    """在这里实现:按 task["type"] / task["query"] 搜索小红书,返回前 task["limit"] 条笔记。
    字段标准见 docs/采集端接口.md。"""
    raise NotImplementedError("请实现真实采集逻辑,或用 --mock 运行")


def mock_author_notes(task: dict) -> list[dict]:
    """博主任务:同一位博主的若干篇笔记,含一篇视频笔记。"""
    out = []
    for i in range(min(task["limit"], 6)):
        nid = f"mockauthor{task['id']}x{i}"
        out.append({
            "note_id": nid, "url": f"https://www.xiaohongshu.com/explore/{nid}",
            "title": f"演示博主的第{i + 1}篇笔记", "content": "这是博主模式的演示正文。\n\n1️⃣ 要点一\n2️⃣ 要点二\n你们怎么看?",
            "author": "演示博主", "author_id": "demo_author_001", "likes": 9000 - i * 1200, "collects": 3000 - i * 400, "comments": 200 - i * 20,
            "images": [], "tags": ["演示"], "type": "video" if i == 5 else "normal",
        })
    return out


def mock_notes(task: dict) -> list[dict]:
    if task["type"] == "author":
        return mock_author_notes(task)
    q, out = task["query"], []
    hooks = ["手残党也能学会", "后悔没早知道", "一周不重样", "亲测有效", "避雷清单", "保姆级教程"]
    for i in range(min(task["limit"], 8)):
        nid = f"mock{task['id']}x{i}"
        out.append({
            "note_id": nid,
            "url": f"https://www.xiaohongshu.com/explore/{nid}",
            "title": f"{q}|{random.choice(hooks)}{random.choice(['!', '✨', '🔥'])}",
            "content": f"姐妹们!关于{q},我踩过的坑都在这了👇\n\n1️⃣ 先说结论……\n2️⃣ 具体步骤……\n3️⃣ 注意事项……\n\n有问题评论区问我~",
            "author": random.choice(["小鹿不加班", "油皮自救", "阿梨测评室", "通勤日记"]),
            "likes": random.choice([f"{random.randint(1, 9)}.{random.randint(0, 9)}万", random.randint(800, 9000)]),
            "collects": random.randint(300, 20000),
            "comments": random.randint(20, 900),
            "images": [],
            "tags": [q, "干货分享", "新手必看"],
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:18765")
    ap.add_argument("--token", default="")
    ap.add_argument("--worker", default="demo")
    ap.add_argument("--interval", type=float, default=5)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--types", default="keyword,title,author", help="本采集端支持的任务类型;不含 author 就领不到博主任务")
    a = ap.parse_args()
    cli = httpx.Client(base_url=a.server, timeout=30,
                       headers={"Authorization": f"Bearer {a.token}"} if a.token else {})
    try:  # 启动自检:地址和 Token 不对就直接退出,不进入轮询
        r = cli.get("/api/ping", params={"worker": a.worker})
        r.raise_for_status()
        print("自检通过:", r.json())
    except Exception as e:
        raise SystemExit(f"自检失败,请检查地址和 Token(见 docs/采集端接口.md 第 2 节):{e}")
    print(f"采集端 {a.worker} 已启动,每 {a.interval}s 拉取一次任务")
    while True:
        try:
            tasks = cli.get("/api/tasks", params={"limit": 1, "worker": a.worker, "types": a.types}).raise_for_status().json()["tasks"]
        except Exception as e:
            print("拉取任务失败:", e)
            time.sleep(a.interval)
            continue
        for t in tasks:
            print(f"领到任务 #{t['id']} [{t['type']}] {t['query']} ×{t['limit']}")
            try:
                notes = mock_notes(t) if a.mock else fetch_notes(t)
                body = {"task_id": t["id"], "notes": notes, "done": True}
            except Exception as e:
                body = {"task_id": t["id"], "notes": [], "error": str(e)}
            r = cli.post("/api/notes", json=body)
            print("  回传:", r.status_code, r.text[:200])
        if not tasks:
            time.sleep(a.interval)


if __name__ == "__main__":
    main()
