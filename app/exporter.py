import json
import re
import secrets
import shutil
import time
import zipfile
from .db import DATA, conn, row_to_dict
from .imagegen import IMG_DIR

EXPORT = DATA / "exports"
EXPORT.mkdir(exist_ok=True)


IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _safe(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", s)[:30] or "untitled"


def export(draft_ids: list[int], progress=None, owner_id: int | None = None) -> str:
    """progress(已处理篇数, 总篇数, 阶段):阶段是 copy(整理文件)或 zip(压缩),供页面显示进度。
    owner_id:谁导出的(登记到 exports,下载时只有本人和管理员能取);文件名带随机串,猜不出别人的包。"""
    total = len(draft_ids)
    stamp = time.strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(3)
    k = 1
    while True:  # 同一秒内再导出一次(连点、两个人同时导出)会撞名:加序号,不再报「文件已存在」
        root = EXPORT / (f"批次_{stamp}" if k == 1 else f"批次_{stamp}_{k}")
        if (EXPORT / f"{root.name}.zip").exists():  # 文件夹用完即删,撞名要看压缩包
            k += 1
            continue
        try:
            root.mkdir(parents=True)
            break
        except FileExistsError:
            k += 1
    manifest = []
    with conn() as c:
        for n, did in enumerate(draft_ids, 1):
            r = c.execute("SELECT * FROM drafts WHERE id=?", (did,)).fetchone()
            if not r:
                continue
            d = row_to_dict(r)
            folder = root / f"{n:02d}_{_safe(d['title'])}"
            (folder / "images").mkdir(parents=True)
            tags = " ".join("#" + t.lstrip("#") for t in d["tags"])
            (folder / "title.txt").write_text(d["title"], encoding="utf-8")
            (folder / "content.txt").write_text(d["content"] + "\n\n" + tags, encoding="utf-8")
            k = 0
            for img in d["images"]:  # 没生成成功的位置是空串,跳过;文件名按实际导出的顺序编号
                src = IMG_DIR / img if img else None
                if src and src.is_file():
                    k += 1
                    shutil.copy(src, folder / "images" / f"{k}{src.suffix or '.png'}")
            manifest.append({"folder": folder.name, "title": d["title"],
                             "alt_titles": d["alt_titles"], "tags": d["tags"]})
            c.execute("UPDATE drafts SET status='exported' WHERE id=?", (did,))
            if progress:
                progress(n, total, "copy")
    (root / "清单.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if progress:
        progress(total, total, "zip")
    zpath = EXPORT / f"{root.name}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in root.rglob("*"):
            if f.is_file():  # 图片本身已经是压缩格式,再压一遍又慢又不省空间:只存不压;文字才压缩
                z.write(f, f.relative_to(root), compress_type=zipfile.ZIP_STORED if f.suffix.lower() in IMG_EXT else zipfile.ZIP_DEFLATED)
    shutil.rmtree(root, ignore_errors=True)  # 压缩包才是交付物,整理用的文件夹留着只会让磁盘翻倍
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO exports(zip, owner_id, draft_ids) VALUES(?,?,?)", (zpath.name, owner_id, json.dumps(draft_ids)))
    prune()
    return zpath.name


def prune(keep_days: int = 14):
    """清理导出目录:有压缩包的批次文件夹(早期版本遗留)直接删;超过 keep_days 天的压缩包和文件夹也删。"""
    now = time.time()
    for p in EXPORT.iterdir():
        try:
            if p.is_dir() and (EXPORT / f"{p.name}.zip").exists() and now - p.stat().st_mtime > 3600:
                shutil.rmtree(p, ignore_errors=True)
            elif now - p.stat().st_mtime > keep_days * 86400:
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink()
        except OSError:
            pass
    with conn() as c:   # 压缩包被清掉了,登记也删掉
        for r in c.execute("SELECT zip FROM exports").fetchall():
            if not (EXPORT / r[0]).exists():
                c.execute("DELETE FROM exports WHERE zip=?", (r[0],))
