"""版本号:部署时由 tools/release.py 写入 app/version.json(不入库);没有这个文件(本地开发)就显示 dev。"""
import json
from pathlib import Path

FILE = Path(__file__).with_name("version.json")


def info() -> dict:
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
        return {"version": str(d.get("version") or "dev"), "commit": str(d.get("commit") or ""), "dirty": bool(d.get("dirty")),
                "built": str(d.get("built") or ""), "subject": str(d.get("subject") or "")}
    except (OSError, ValueError):
        return {"version": "dev", "commit": "", "dirty": False, "built": "", "subject": ""}
