"""笔记入库:采集端回传和管理端导入共用同一套字段标准(见 docs/采集端接口.md)。"""
import csv
import datetime
import hashlib
import io
import itertools
import json
import re
from .db import conn

# 导入表格时,中文表头 → 标准字段
HEADER_MAP = {
    "标题": "title", "正文": "content", "内容": "content", "作者": "author", "博主": "author",
    "点赞": "likes", "点赞数": "likes", "收藏": "collects", "收藏数": "collects", "评论": "comments", "评论数": "comments",
    "链接": "url", "笔记链接": "url", "封面": "cover", "图片": "images", "配图": "images",
    "话题": "tags", "标签": "tags", "笔记id": "note_id", "笔记ID": "note_id", "id": "note_id",
    "类型": "type", "发布时间": "published_at", "作者ID": "author_id", "作者主页": "author_url",
}


def num(v) -> int:
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"([\d.]+)\s*(万|w|W|k|K)?", str(v or ""))
    if not m:
        return 0
    n, u = float(m.group(1)), m.group(2)
    return int(n * (10000 if u in ("万", "w", "W") else 1000 if u in ("k", "K") else 1))


ZERO_WIDTH = re.compile("[\u200b\u2060\ufeff]")  # 不含 U+200D:它是 emoji 的组合符(如 🙋‍♀️),删了会让 emoji 变形
AUTHOR_ID = re.compile(r"/user/profile/([0-9A-Za-z]+)")


def clean_text(s: str) -> str:
    return ZERO_WIDTH.sub("", s or "").replace("\u00a0", " ")


def author_id_of(n: dict) -> str:
    """采集端没传 author_id 时,从主页链接 author_url 里提取。"""
    m = AUTHOR_ID.search(str(n.get("author_url") or ""))
    return str(n.get("author_id") or "") or (m.group(1) if m else "")


def norm_date(text: str, ref: datetime.date) -> str:
    """把小红书页面上的各种发布时间写法统一成 YYYY-MM-DD:
    「编辑于 2025-11-05」「09-02」(没写年份按 ref 推断)「3天前」「昨天 12:30」「刚刚」。认不出来就原样返回。"""
    t = re.sub(r"^(编辑于|发布于|修改于)\s*", "", (text or "").strip())
    m = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", t)
    try:
        if m:
            return datetime.date(*map(int, m.groups())).isoformat()
        m = re.search(r"(?<!\d)(\d{1,2})[-/.月](\d{1,2})(?!\d)", t)
        if m:
            d = datetime.date(ref.year, int(m[1]), int(m[2]))
            return (d.replace(year=ref.year - 1) if d > ref else d).isoformat()
    except ValueError:
        return t
    if re.search(r"刚刚|今天|分钟前|小时前", t):
        return ref.isoformat()
    if "昨天" in t:
        return (ref - datetime.timedelta(days=1)).isoformat()
    if "前天" in t:
        return (ref - datetime.timedelta(days=2)).isoformat()
    m = re.search(r"(\d+)\s*天前", t)
    return (ref - datetime.timedelta(days=int(m[1]))).isoformat() if m else t


def normalize_existing() -> int:
    """给已入库的笔记补做清洗:去零宽字符、统一发布时间、补博主 ID。可重复执行,原始数据 raw 不动。"""
    n = 0
    with conn() as c:
        for r in c.execute("SELECT id,title,content,author,author_id,published_at,raw,created FROM notes").fetchall():
            try:
                raw = json.loads(r["raw"] or "{}")
            except ValueError:
                raw = {}
            try:
                ref = datetime.date.fromisoformat((r["created"] or "")[:10])
            except ValueError:
                ref = datetime.date.today()
            new = (clean_text(r["title"]), clean_text(r["content"]), clean_text(r["author"]),
                   r["author_id"] or author_id_of(raw)[:64], norm_date(str(raw.get("published_at") or r["published_at"]), ref))
            if new != (r["title"], r["content"], r["author"], r["author_id"], r["published_at"]):
                c.execute("UPDATE notes SET title=?, content=?, author=?, author_id=?, published_at=? WHERE id=?", (*new, r["id"]))
                n += 1
    mark_duplicates()
    return n


# ---------- 重复笔记:换号搬运、同一模板反复发的帖子 ----------
def _norm(t: str) -> str:
    """只留文字和数字(去掉表情、标点、空白),比较时不受这些差异影响。"""
    return re.sub(r"[\W_]+", "", (t or "").lower())


def _grams(t: str, k: int = 3) -> set:
    return {t[i:i + k] for i in range(max(0, len(t) - k + 1))}


def mark_duplicates() -> int:
    """把重复的笔记归成一组,互动量最高的那篇当代表,其余记 dup_of=代表的 id。返回被标为重复的篇数。
    判断规则(按文字,不按意思,避免把两篇不同的腐乳做法当成重复):
    - 标题去掉表情和标点后完全一样(至少 6 个字):换号搬运;
    - 同一个博主,标题几乎一样(3 字片段重合 ≥ 80%):同一模板反复发;
    - 正文去掉表情和标点后开头、结尾都一样(正文至少 60 字):整篇照搬。"""
    with conn() as c:
        rows = [dict(r) for r in c.execute("SELECT id,title,content,author,author_id,likes,collects,comments,dup_of FROM notes")]
    parent = {r["id"]: r["id"] for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    by_title, by_body, by_author = {}, {}, {}
    for r in rows:
        t, body = _norm(r["title"]), _norm(r["content"])
        if len(t) >= 6:
            by_title.setdefault(t, []).append(r["id"])
        if len(body) >= 60:
            by_body.setdefault(body[:30] + body[-30:], []).append(r["id"])
        a = r["author_id"] or r["author"]
        if a:
            by_author.setdefault(a, []).append(r)
    for g in [*by_title.values(), *by_body.values()]:
        for x in g[1:]:
            union(g[0], x)
    for g in by_author.values():
        for x, y in itertools.combinations(g, 2):
            A, B = _grams(_norm(x["title"])), _grams(_norm(y["title"]))
            if A and B and len(A & B) / len(A | B) >= 0.8:
                union(x["id"], y["id"])
    groups = {}
    for r in rows:
        groups.setdefault(find(r["id"]), []).append(r)
    eng = lambda r: (r["likes"] or 0) + (r["collects"] or 0) + (r["comments"] or 0)
    changes = []
    for g in groups.values():
        rep = max(g, key=lambda r: (eng(r), -r["id"]))
        for r in g:
            want = None if r is rep else rep["id"]
            if r["dup_of"] != want:
                changes.append((want, r["id"]))
    if changes:
        with conn() as c:
            c.executemany("UPDATE notes SET dup_of=? WHERE id=?", changes)
    return sum(1 for g in groups.values() for _ in g[1:])


# 工作台已经使用的字段;采集端传的其他字段不丢,保存在 raw 里
KNOWN_FIELDS = {"note_id", "url", "title", "content", "author", "author_id", "author_url", "likes", "collects", "comments",
                "cover", "images", "tags", "type", "published_at", "task_id"}
RAW_MAX = 200_000  # 单篇原始数据上限(字符);超过时把超长的值换成占位说明,字段名仍保留


def _raw_json(old: str | None, n: dict) -> str:
    try:
        merged = {**json.loads(old or "{}"), **n}
    except ValueError:
        merged = dict(n)
    text = json.dumps(merged, ensure_ascii=False, default=str)
    if len(text) > RAW_MAX:
        merged = {k: (v if len(json.dumps(v, ensure_ascii=False, default=str)) < 20_000 else f"(内容过长,未保存,{len(json.dumps(v, default=str))} 字符)")
                  for k, v in merged.items()}
        text = json.dumps(merged, ensure_ascii=False, default=str)
    return text


def unknown_fields() -> list[dict]:
    """采集端传了、但工作台没有专门使用的字段,及出现在多少篇笔记里。"""
    counts: dict[str, int] = {}
    with conn() as c:
        for (raw,) in c.execute("SELECT raw FROM notes WHERE raw!='{}' AND raw!=''"):
            try:
                keys = json.loads(raw).keys()
            except ValueError:
                continue
            for k in keys:
                if k not in KNOWN_FIELDS:
                    counts[k] = counts.get(k, 0) + 1
    return [{"k": k, "n": v} for k, v in sorted(counts.items(), key=lambda x: -x[1])]


def save_notes(notes: list[dict], *, project_id: int | None = None, task_id: int | None = None,
               track: str = "", keyword: str = "", source: str = "collector", worker: str = "") -> dict:
    """写入全局笔记库。已存在的帖子(按 uid)只刷新互动数据并补全空字段,不重复入库;
    传了 project_id 就同时关联到该项目。返回 new 新入库 / updated 已有并刷新 / linked 新关联到项目。"""
    out = {"new": 0, "updated": 0, "linked": 0, "skipped": 0}
    with conn() as c:
        for n in notes:
            title = clean_text(str(n.get("title") or "")).strip()
            content = clean_text(str(n.get("content") or ""))
            if not title and not content:
                out["skipped"] += 1
                continue
            uid = str(n.get("note_id") or n.get("url") or hashlib.md5((title + content).encode()).hexdigest())
            images = n.get("images") or []
            if isinstance(images, str):
                images = re.split(r"[\s,,]+", images)
            images = [u for u in images if isinstance(u, str) and u.startswith("http")][:18]
            tags = n.get("tags") or []
            if isinstance(tags, str):
                tags = re.findall(r"#?([^\s#,,]+)", tags)
            tags = [str(t).lstrip("#") for t in tags][:20]
            likes, collects, comments = num(n.get("likes")), num(n.get("collects")), num(n.get("comments"))
            cover = n.get("cover") or (images[0] if images else "")
            aid = author_id_of(n)[:64]
            published = norm_date(str(n.get("published_at") or ""), datetime.date.today())
            row = c.execute("SELECT id, images, mirror_status, raw FROM notes WHERE uid=?", (uid,)).fetchone()
            raw = _raw_json(row["raw"] if row else None, n)
            if row:
                nid = row["id"]
                c.execute("UPDATE notes SET raw=? WHERE id=?", (raw, nid))
                c.execute("UPDATE notes SET author_id=? WHERE id=? AND author_id=''", (aid, nid))
                c.execute("""UPDATE notes SET likes=MAX(likes,?), collects=MAX(collects,?), comments=MAX(comments,?),
                    content=CASE WHEN content='' THEN ? ELSE content END, cover=CASE WHEN cover='' THEN ? ELSE cover END,
                    images=CASE WHEN images='[]' THEN ? ELSE images END, url=CASE WHEN url='' THEN ? ELSE url END,
                    updated=CURRENT_TIMESTAMP WHERE id=?""",
                          (likes, collects, comments, content, cover, json.dumps(images), n.get("url") or "", nid))
                # 上次转存没成功(多半是链接过期)时,用这次的新链接替换并重新转存
                if images and (row["images"] in ("[]", "") or row["mirror_status"] in ("error", "partial")):
                    c.execute("UPDATE notes SET images=?, cover=?, images_local='[]', cover_local='', mirror_status='pending',"
                              " mirror_error='' WHERE id=?", (json.dumps(images), cover, nid))
                out["updated"] += 1
            else:
                nid = c.execute(
                    "INSERT INTO notes(uid,url,title,content,author,author_id,likes,collects,comments,cover,images,tags,note_type,"
                    "published_at,track,keyword,source,task_id,worker,raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uid, n.get("url") or "", title, content, clean_text(str(n.get("author") or "")), aid, likes, collects, comments, cover,
                     json.dumps(images), json.dumps(tags, ensure_ascii=False), n.get("type") or "normal",
                     published, track, keyword, source, task_id, worker, raw)).lastrowid
                out["new"] += 1
            if project_id:
                out["linked"] += c.execute("INSERT OR IGNORE INTO project_notes(project_id,note_id,task_id) VALUES(?,?,?)",
                                           (project_id, nid, task_id)).rowcount
    if out["new"]:
        mark_duplicates()
    return out


def localize(n: dict) -> dict:
    """已转存的图片对外换成本地地址,没转存成功的仍用原链接。"""
    loc, imgs = n.get("images_local") or [], n.get("images") or []
    if loc and len(loc) == len(imgs):
        n["images"] = [f"/note_images/{l}" if l else u for u, l in zip(imgs, loc)]
    if n.get("cover_local"):
        n["cover"] = "/note_images/" + n["cover_local"]
    return n


def _rows_to_notes(rows: list[list]) -> list[dict]:
    if not rows:
        return []
    head = [HEADER_MAP.get(str(h or "").strip(), str(h or "").strip()) for h in rows[0]]
    return [{k: v for k, v in zip(head, r) if k and v not in (None, "")} for r in rows[1:]]


INTENT = re.compile(r"教程|做法|怎么吃|怎么做|怎么选|怎么|如何|攻略|推荐|测评|评测|避坑|清单|自制|在家|技巧|方法|食谱|步骤|合集|对比|入门|新手|好物|种草|体验|心得|分享|大全|公式|搭配|吃法|冲泡|冲调|制作")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def core_terms(keyword: str) -> list[str]:
    """搜索词里去掉「教程、做法」这类意图词,剩下的就是它真正在找的东西。"""
    out = []
    for t in re.split(r"[,，、;；/\s]+", keyword or ""):
        t = t.strip()
        if not t or t.startswith("博主:"):
            continue
        c = INTENT.sub("", t).strip("做的 ")
        out.append(c if len(c) >= 2 else t)
    return out


def _shares(a: str, b: str) -> bool:
    return any(a[i:i + 2] in b for i in range(len(a) - 1))


def relevance(n: dict) -> tuple[str, str]:
    """这篇笔记和采集它时用的搜索词相不相关。返回 (high / low, 原因)。
    已有 AI 主题:看主题和搜索词有没有重合;还没有主题:看搜索词出现在标题 / 话题 / 正文的哪里。"""
    terms = core_terms(n.get("keyword"))
    if not terms:
        return "high", ""
    tags = " ".join(n.get("tags") or [])
    where = [name for name, text in (("标题", n.get("title") or ""), ("话题", tags), ("正文", n.get("content") or "")) if any(t in text for t in terms)]
    topic, tm = n.get("topic") or "", "、".join(terms)
    if topic and topic != "未识别":
        if any(_shares(t, topic) or _shares(topic, t) for t in terms):
            return "high", ""
        return "low", f"笔记讲的是「{topic}」,和搜索词「{tm}」无关(搜索词出现在:{'、'.join(where) or '都没有'})"
    if "标题" in where or "话题" in where:
        return "high", ""
    return "low", (f"搜索词「{tm}」只在{'、'.join(where)}里被提到" if where else f"标题、话题、正文里都没有搜索词「{tm}」")


def is_old(published_at: str, max_age_days: int, today: datetime.date | None = None) -> bool:
    """发布时间看得懂,并且早于 max_age_days 天前才算「太旧」;0 表示不限,看不懂的不算旧。"""
    if not max_age_days or not ISO_DATE.match(published_at or ""):
        return False
    try:
        return ((today or datetime.date.today()) - datetime.date.fromisoformat(published_at)).days > max_age_days
    except ValueError:
        return False


def split_by_age(notes: list, max_age_days: int, today: datetime.date | None = None) -> tuple[list, int]:
    """采集端回传时的兜底过滤:发布时间太旧的丢掉(返回丢了几篇),看不懂发布时间的保留。"""
    today, kept, dropped = today or datetime.date.today(), [], 0
    for n in notes:
        d = norm_date(str(n.get("published_at") or ""), today) if isinstance(n, dict) else ""
        if is_old(d, max_age_days, today):
            dropped += 1
        else:
            kept.append(n)
    return kept, dropped


def annotate(n: dict, max_age_days: int) -> dict:
    """给页面用的派生字段:弱相关(rel / rel_why)、是否超出时间范围(old)。"""
    n["rel"], n["rel_why"] = relevance(n)
    n["old"] = is_old(n.get("published_at"), max_age_days)
    return n


def _unpack(n):
    """导入本工作台导出的备份时,把嵌套的 raw 还原成平铺的原始字段,再叠上标准字段;
    ai / projects / images_local 这些是备份附带的说明,不当作采集字段。"""
    if isinstance(n, dict) and isinstance(n.get("raw"), dict):
        return {**n["raw"], **{k: v for k, v in n.items() if k in KNOWN_FIELDS}}
    return n


def parse_file(filename: str, raw: bytes) -> list[dict]:
    """支持 .json(数组或 {"notes":[...]})、.xlsx、.csv;表头可用中文或标准字段名。"""
    name = filename.lower()
    if name.endswith(".json"):
        data = json.loads(raw.decode("utf-8-sig"))
        return [_unpack(n) for n in (data.get("notes", []) if isinstance(data, dict) else data)]
    if name.endswith(".csv"):
        return _rows_to_notes(list(csv.reader(io.StringIO(raw.decode("utf-8-sig")))))
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(raw), read_only=True, data_only=True).active
        return _rows_to_notes([list(r) for r in ws.iter_rows(values_only=True)])
    raise ValueError("只支持 .json / .xlsx / .csv")
