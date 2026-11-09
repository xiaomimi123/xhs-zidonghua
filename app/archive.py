"""笔记库备份:把笔记(含原始数据、AI 标签、图片)导出成一个压缩包,留作长期资料。
包里的 notes.json / notes.csv 用的是「导入」功能认识的字段,以后可以直接重新导入。"""
import csv
import io
import json
import re
import time
import zipfile
from pathlib import Path

from . import mirror
from .db import DATA, q_all, tier_sql

ARCHIVE = DATA / "archive"
ARCHIVE.mkdir(exist_ok=True)
TIER = {"viral": "爆款", "hot": "热门", "normal": "普通"}
README = """小红书流水线工作台 · 笔记库备份
================================

notes.json   完整数据(推荐留存)。每篇笔记含:标题、正文、作者、互动数据、话题、发布时间、
             AI 标签、所属赛道/关键词/项目、采集端原始数据(raw)等。
             images 是采集时的原始图片链接(会过期);images_local 是本包内图片的相对路径,不会过期。
notes.csv    表格版,用 Excel / WPS 直接打开(UTF-8 带 BOM)。
images/      每篇笔记的配图,按「笔记ID/序号」存放。

重新导入:在工作台管理端「笔记库 → 导入笔记」选择 notes.json 或 notes.csv 即可
(字段与采集端回传标准一致;图片链接已过期时,以包里的 images/ 为准)。
"""
CSV_COLS = ["标题", "正文", "作者", "作者ID", "点赞", "收藏", "评论", "链接", "话题", "笔记ID", "发布时间", "类型",
            "赛道", "关键词", "主题", "内容类型", "目标人群", "爆点", "分级", "采集时间", "图片数"]


def _safe(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]", "_", s or "")[:40] or "note"


def export_notes(ids: list[int] | None = None, with_images: bool = True) -> dict:
    where, args = "", []
    if ids:
        where, args = f"WHERE n.id IN ({','.join('?' * len(ids))})", list(ids)
    rows = q_all(f"SELECT n.*, {tier_sql()} AS tier FROM notes n {where} ORDER BY n.id", *args)
    projs: dict[int, list] = {}
    for r in q_all("SELECT pn.note_id, p.id, p.track, p.keywords FROM project_notes pn JOIN projects p ON p.id=pn.project_id"):
        projs.setdefault(r["note_id"], []).append({"id": r["id"], "track": r["track"], "keywords": r["keywords"]})
    base = f"笔记库_{time.strftime('%Y%m%d_%H%M%S')}_{len(rows)}篇"
    name, k = base + ".zip", 1
    while (ARCHIVE / name).exists():  # 同一秒内再导出一次也不会覆盖已有的备份
        k += 1
        name = f"{base}_{k}.zip"
    path = ARCHIVE / name
    items, n_img = [], 0
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLS)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n in rows:
            local = []
            if with_images:
                for k, fn in enumerate(n["images_local"] or [], 1):
                    f = mirror.DIR / fn if fn else None
                    if f and f.is_file():
                        arc = f"images/{_safe(n['uid'])}/{k}{f.suffix}"
                        z.write(f, arc)
                        local.append(arc)
                        n_img += 1
                    else:
                        local.append("")
            try:
                raw = json.loads(n["raw"] or "{}")
            except ValueError:
                raw = {}
            item = {
                "note_id": n["uid"], "url": n["url"], "title": n["title"], "content": n["content"], "author": n["author"],
                "author_id": n["author_id"], "likes": n["likes"], "collects": n["collects"], "comments": n["comments"],
                "tags": n["tags"], "published_at": n["published_at"], "type": n["note_type"], "cover": n["cover"],
                "images": n["images"], "images_local": local,
                "tier": TIER.get(n["tier"], n["tier"]), "track": n["track"], "keyword": n["keyword"], "topic": n["topic"], "source": n["source"],
                "worker": n["worker"], "collected_at": n["created"], "updated_at": n["updated"],
                "ai": {"topic": n["topic"], "type": n["ai_type"], "audience": n["ai_audience"], "highlight": n["ai_highlight"], "tags": n["ai_tags"]},
                "projects": projs.get(n["id"], []), "raw": raw,
            }
            items.append(item)
            w.writerow([n["title"], n["content"], n["author"], n["author_id"], n["likes"], n["collects"], n["comments"], n["url"],
                        " ".join("#" + t for t in n["tags"]), n["uid"], n["published_at"], n["note_type"], n["track"], n["keyword"], n["topic"],
                        n["ai_type"], n["ai_audience"], n["ai_highlight"], TIER.get(n["tier"], n["tier"]), n["created"], len(n["images"])])
        z.writestr("notes.json", json.dumps({"exported_at": time.strftime("%Y-%m-%d %H:%M:%S"), "count": len(items), "notes": items},
                                            ensure_ascii=False, indent=1))
        z.writestr("notes.csv", "﻿" + buf.getvalue())
        z.writestr("README.txt", README)
    return {"name": name, "notes": len(rows), "images": n_img, "size_mb": round(path.stat().st_size / 1048576, 1)}


def list_archives() -> list[dict]:
    fs = sorted((f for f in ARCHIVE.glob("*.zip")), key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"name": f.name, "size_mb": round(f.stat().st_size / 1048576, 1),
             "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime))} for f in fs]
