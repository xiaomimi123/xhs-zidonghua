"""发布 / 回滚:每次部署都带上版本号,出了问题能按版本号退回去。

    python tools/release.py build            写入版本号 → docker compose build → 给镜像打上版本标签(不重启)
    python tools/release.py list             看已有的版本(镜像标签)和对应的提交
    python tools/release.py rollback v1.58   把线上退回到某个版本(镜像换回去并重启容器,数据不动)

版本号 = v1.{提交总数},每个提交一个号,单调递增,和 git 提交一一对应(`git log` 能查到);
带「+」表示构建时还有没提交的改动,这种版本无法精确回到源码,所以部署前先提交。
写入 app/version.json(不入库),页面左下角和 /api/version 显示它。"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "app" / "version.json"
IMAGE = "xhs-workbench"
KEEP = 12   # 保留最近多少个版本的镜像(旧的清掉,免得占盘)


def run(*cmd, check=True, capture=True) -> str:
    r = subprocess.run(cmd, cwd=ROOT, capture_output=capture, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise SystemExit(f"命令失败:{' '.join(cmd)}\n{(r.stderr or r.stdout).strip()}")
    return (r.stdout or "").strip()


def git_info() -> dict:
    n = int(run("git", "rev-list", "--count", "HEAD"))
    commit = run("git", "rev-parse", "--short", "HEAD")
    dirty = bool([ln for ln in run("git", "status", "--porcelain").splitlines() if "app/version.json" not in ln])
    return {"version": f"v1.{n}", "commit": commit, "dirty": dirty, "built": time.strftime("%Y-%m-%d %H:%M"),
            "subject": run("git", "log", "-1", "--format=%s")}


def tag_name(info: dict) -> str:
    return info["version"] + ("-dirty" if info["dirty"] else "")


def cmd_build():
    info = git_info()
    VERSION_FILE.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"版本 {info['version']}{'+' if info['dirty'] else ''}  提交 {info['commit']}  {info['subject']}")
    if info["dirty"]:
        print("  ⚠ 有没提交的改动:这个版本无法精确回到源码,建议先提交再部署")
    subprocess.run(["docker", "compose", "build"], cwd=ROOT, check=True)
    tag = f"{IMAGE}:{tag_name(info)}"
    run("docker", "tag", f"{IMAGE}:latest", tag)
    print(f"镜像已打标签 {tag}。部署:docker compose up -d")
    prune()


def versions() -> list[tuple[str, str]]:
    out = run("docker", "images", IMAGE, "--format", "{{.Tag}}\t{{.CreatedSince}}")
    rows = [ln.split("\t") for ln in out.splitlines() if ln.startswith("v1.")]
    return sorted(rows, key=lambda r: int(r[0][3:].split("-")[0]), reverse=True)


def prune():
    for tag, _ in versions()[KEEP:]:
        run("docker", "rmi", f"{IMAGE}:{tag}", check=False)


def cmd_list():
    cur = run("docker", "inspect", "-f", "{{.Image}}", "xhs-workbench", check=False)
    rows = versions()
    if not rows:
        print("还没有带版本标签的镜像。先运行:python tools/release.py build")
    for tag, since in rows:
        commit = run("git", "log", "-1", "--format=%h %s", tag.split("-")[0], check=False) or "(没有同名的 git 标签,按版本号对应第 N 个提交)"
        print(f"{tag:14} {since:16} {commit}")


def cmd_rollback(ver: str):
    cands = [t for t, _ in versions() if t == ver or t.split("-")[0] == ver]
    if not cands:
        raise SystemExit(f"没有 {ver} 的镜像。已有版本:{', '.join(t for t, _ in versions()) or '无'}")
    print(f"回滚到 {cands[0]}:先在容器里备份数据库,再换镜像、重启(数据不动)")
    run("docker", "exec", "xhs-workbench", "python", "-c",
        "import sqlite3,time;s=sqlite3.connect('/app/data/workbench.db');d=sqlite3.connect('/app/data/backup-'+time.strftime('%Y%m%d-%H%M%S')+'-before-rollback.db');s.backup(d);d.close()", check=False)
    run("docker", "tag", f"{IMAGE}:{cands[0]}", f"{IMAGE}:latest")
    subprocess.run(["docker", "compose", "up", "-d"], cwd=ROOT, check=True)
    print("已回滚。注意:数据库只会往前升级,旧版本代码能读新库,新增的列它会忽略;如果要连数据一起退回,用 data/backup-*.db。")


if __name__ == "__main__":
    a = sys.argv[1:] or ["list"]
    if a[0] == "build":
        cmd_build()
    elif a[0] == "list":
        cmd_list()
    elif a[0] == "rollback" and len(a) > 1:
        cmd_rollback(a[1])
    else:
        print(__doc__)
