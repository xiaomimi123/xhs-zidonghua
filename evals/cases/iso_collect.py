"""采集端回传与入库:博主模式、原始数据保留、关键词与清洗。"""
import json
from app import db


def run(rep, env):
    col, q, stats, user = env.col, env.q, env.stats, env.user
    print("\n[博主模式]")
    pid = user.post("/api/projects", json={"track": "美食", "keywords": ["减脂餐"], "direction": ""}).json()["id"]
    url = "https://www.xiaohongshu.com/user/profile/abc123"
    rep.check("追加博主任务", user.post(f"/api/projects/{pid}/crawl", json={"type": "author", "query": url}).status_code == 200)
    for name, body in [("未知类型", {"type": "foo", "query": "x"}), ("空内容", {"type": "author", "query": "  "}),
                       ("多行内容", {"type": "author", "query": "a\nb"}), ("过长", {"type": "author", "query": "x" * 301})]:
        rep.check(f"拒绝非法任务:{name}", user.post(f"/api/projects/{pid}/crawl", json=body).status_code == 400)
    # 旧采集端不带 types:只能领到关键词/标题任务
    got = col.get("/api/tasks", params={"limit": 20, "worker": "old"}).json()["tasks"]
    rep.check("不带 types:只发关键词/标题", got and all(t["type"] in ("keyword", "title") for t in got), str(got))
    rep.check("不带 types:博主任务仍在队列里", len(q("SELECT id FROM crawl_tasks WHERE status='pending' AND type='author'")) == 1)
    rep.check("types 写错时退回默认", all(t["type"] != "author" for t in col.get("/api/tasks", params={"types": "bogus"}).json()["tasks"]))
    got = col.get("/api/tasks", params={"limit": 20, "worker": "new", "types": "keyword,title,author"}).json()["tasks"]
    rep.check("声明 types=author 才领到博主任务", [t["type"] for t in got] == ["author"] and got[0]["query"] == url, str(got))
    tid = got[0]["id"]

    notes = [
        {"note_id": "B1", "title": "图文一", "content": "正文" * 20, "likes": 5000, "collects": 2000, "comments": 100, "author": "小鹿", "author_id": "abc123", "images": []},
        {"note_id": "B2", "title": "视频一", "content": "视频文案", "likes": 90000, "collects": 100, "comments": 5, "author": "小鹿", "author_id": "abc123", "type": "video"},
    ]
    res = col.post("/api/notes", json={"task_id": tid, "notes": notes}).json()["results"][0]
    rows = {n["uid"]: n for n in q("SELECT * FROM notes")}
    rep.check("博主笔记入库(含视频)", set(rows) == {"B1", "B2"} and res["status"] == "done", str(res))
    rep.check("关键词记成「博主:昵称」", rows["B1"]["keyword"] == "博主:小鹿", rows["B1"]["keyword"])
    rep.check("保存 author_id 与视频类型", rows["B1"]["author_id"] == "abc123" and rows["B2"]["note_type"] == "video")
    shown = {n["title"]: n for n in user.get(f"/api/projects/{pid}/notes").json()}
    rep.check("项目笔记接口带出视频标记", shown["视频一"]["note_type"] == "video")
    st = stats.from_groups("美食", [dict(n, tags=[], images=[]) for n in rows.values() if n["note_type"] != "video"], [], [])
    rep.check("统计的参考组不含视频", st["ref"]["n"] == 1)
    built = stats.build("美食", [dict(n) for n in rows.values()])
    rep.check("stats.build 过滤视频笔记", built["ref"]["n"] == 1 and (built["top"] or {"n": 0})["n"] == 1, str({k: (v or {}).get("n") for k, v in built.items() if k in ("ref", "top", "normal")}))

    print("\n[原始数据保留]")
    from app.notes import RAW_MAX, unknown_fields
    post = lambda n: col.post("/api/notes", json={"task_id": tid, "notes": [n]})
    raw_of = lambda uid: json.loads(q("SELECT raw FROM notes WHERE uid=?", uid)[0]["raw"])
    post({"note_id": "R1", "title": "原始数据测试", "content": "正文", "likes": 10, "ip_location": "广东", "fans": 1200, "extra": {"a": [1, 2]}})
    r1 = raw_of("R1")
    rep.check("未知字段也完整保存", r1.get("ip_location") == "广东" and r1.get("fans") == 1200 and r1.get("extra") == {"a": [1, 2]}, str(r1))
    post({"note_id": "R1", "title": "原始数据测试", "content": "正文", "likes": 20, "ip_location": "北京", "new_field": "x"})
    r2 = raw_of("R1")
    rep.check("重复回传按字段合并,同名取最新", r2.get("ip_location") == "北京" and r2.get("fans") == 1200 and r2.get("new_field") == "x", str(r2))
    names = {f["k"] for f in unknown_fields()}
    rep.check("统计未使用字段", {"ip_location", "fans", "extra", "new_field"} <= names and not ({"title", "likes", "content"} & names), str(names))
    post({"note_id": "R2", "title": "超大字段", "content": "x", "likes": 1, "blob": "y" * 300_000})
    r3 = raw_of("R2")
    rep.check("超大字段不撑爆数据库,字段名保留", "未保存" in str(r3.get("blob")) and len(json.dumps(r3, ensure_ascii=False)) < RAW_MAX, str(r3)[:80])
    rep.check("已用字段不受原始数据影响", q("SELECT likes, content FROM notes WHERE uid='R1'")[0]["likes"] == 20)

    print("\n[关键词与清洗入库]")
    import datetime
    from app.notes import normalize_existing
    pid2 = user.post("/api/projects", json={"track": "美食", "keywords": ["腐乳，咖啡,黄姜粉、减脂餐；早餐 "], "direction": ""}).json()["id"]
    kws = next(p["keywords"] for p in user.get("/api/projects").json() if p["id"] == pid2)
    rep.check("一个框里用逗号写多个词会拆开", kws == ["腐乳", "咖啡", "黄姜粉", "减脂餐", "早餐"], str(kws))
    col.post("/api/notes", json={"task_id": tid, "notes": [{"note_id": "C1", "title": "清​洗", "content": "正​文\U0001F64B‍♀", "likes": 1,
             "published_at": "编辑于 09-02", "author": "作者", "author_url": "https://www.xiaohongshu.com/user/profile/abc999"}]})
    row = q("SELECT * FROM notes WHERE uid='C1'")[0]
    rep.check("入库时去零宽字符、保留 emoji", row["title"] == "清洗" and row["content"] == "正文\U0001F64B‍♀", repr(row["content"]))
    rep.check("入库时统一发布时间", row["published_at"] == f"{datetime.date.today().year if datetime.date(datetime.date.today().year, 9, 2) <= datetime.date.today() else datetime.date.today().year - 1}-09-02", row["published_at"])
    rep.check("入库时提取博主 ID", row["author_id"] == "abc999", row["author_id"])
    rep.check("原始数据里保留的是未处理的原文", json.loads(row["raw"])["published_at"] == "编辑于 09-02" and "​" in json.loads(row["raw"])["title"])
    db.q_exec("UPDATE notes SET published_at='编辑于 09-02', author_id='', title='旧​标题' WHERE uid='C1'")
    normalize_existing()
    row = q("SELECT * FROM notes WHERE uid='C1'")[0]
    rep.check("给已入库笔记补做清洗", row["author_id"] == "abc999" and row["title"] == "旧标题" and row["published_at"].endswith("-09-02"), str(row["published_at"]))
    rep.check("补做清洗可重复执行", normalize_existing() == 0)

    for _k in ('body', 'f', 'got', 'k', 'n', 'name', 'names', 'notes', 'p', 'pid', 'r1', 'r2', 'r3', 'res', 'rows', 't', 'url', 'v',):
        if _k in locals():
            setattr(env, _k, locals()[_k])
