"""导出:同一秒连续导出、后台导出任务。"""
import json
import os
import time
from ..common import _raises2
from app import db
from pathlib import Path


def run(rep, env):
    _tf12, au13, ppj = env._tf12, env.au13, env.ppj
    # 导出:同一秒连续导出不冲突,图片只存不压
    import zipfile as _zf21
    from app import exporter as ex21
    o_exp21, o_img21 = ex21.EXPORT, ex21.IMG_DIR
    ex21.EXPORT, ex21.IMG_DIR = Path(_tf12.mkdtemp()), Path(_tf12.mkdtemp())
    (ex21.IMG_DIR / "e1.png").write_bytes(b"\x89PNG" + b"0" * 5000)
    d21e = db.q_exec("INSERT INTO drafts(project_id,title,content,tags,images,status) VALUES(?,?,?,?,?,'ready')", ppj, "导出测试", "正文" * 50, json.dumps(["话题"]), json.dumps(["e1.png"])).lastrowid
    try:
        z1 = ex21.export([d21e])
        z2 = ex21.export([d21e])
        z3 = ex21.export([d21e])
        info21 = {i.filename.split("/")[-1]: i.compress_type for i in _zf21.ZipFile(ex21.EXPORT / z1).infolist()}
    finally:
        names21 = sorted(p.name for p in ex21.EXPORT.iterdir())
        ex21.EXPORT, ex21.IMG_DIR = o_exp21, o_img21
    rep.check("导出:同一秒连续导出多次不冲突,每次都有自己的包", len({z1, z2, z3}) == 3 and sum(n.endswith(".zip") for n in names21) == 3, str(names21))
    rep.check("导出:压缩后不留批次文件夹(不让磁盘翻倍)", all(n.endswith(".zip") for n in names21), str(names21))
    _pe = Path(_tf12.mkdtemp()); o_pe = ex21.EXPORT; ex21.EXPORT = _pe
    try:
        (_pe / "批次_old").mkdir(); (_pe / "批次_old.zip").write_bytes(b"x"); (_pe / "批次_x.zip").write_bytes(b"x")
        _old = time.time() - 2 * 3600
        os.utime(_pe / "批次_old", (_old, _old))
        _ancient = time.time() - 20 * 86400
        os.utime(_pe / "批次_x.zip", (_ancient, _ancient))
        ex21.prune()
        left = sorted(p.name for p in _pe.iterdir())
    finally:
        ex21.EXPORT = o_pe
    rep.check("导出清理:遗留文件夹和过期压缩包被清掉,新压缩包保留", left == ["批次_old.zip"], str(left))
    rep.check("导出:图片只存不压(已经是压缩格式),文字才压缩", info21.get("1.png") == _zf21.ZIP_STORED and info21.get("content.txt") == _zf21.ZIP_DEFLATED, str(info21))
    # 导出后台任务:有进度,完成后给出文件名;空选择拒绝
    o_exp22, o_img22 = ex21.EXPORT, ex21.IMG_DIR
    ex21.EXPORT, ex21.IMG_DIR = Path(_tf12.mkdtemp()), Path(_tf12.mkdtemp())
    (ex21.IMG_DIR / "e1.png").write_bytes(b"\x89PNG" + b"0" * 5000)
    d22e = [db.q_exec("INSERT INTO drafts(project_id,title,content,tags,images,status) VALUES(?,?,?,?,?,'ready')", ppj, f"进度测试{i}", "正文", "[]", json.dumps(["e1.png"])).lastrowid for i in range(3)]
    try:
        j22 = au13.export_start(au13.ExportReq(ids=d22e + d22e[:1]))
        seen22, t_end22 = [], __import__("time").time() + 20
        while __import__("time").time() < t_end22:
            s22 = au13.export_status(j22["job"])
            seen22.append((s22["done"], s22["stage"]))
            if s22["state"] != "running":
                break
            __import__("time").sleep(0.01)
        zip22 = (ex21.EXPORT / s22["zip"]).is_file() if s22["zip"] else False
    finally:
        ex21.EXPORT, ex21.IMG_DIR = o_exp22, o_img22
    rep.check("导出任务:立即返回任务号,总数去重(3 篇),完成后给出压缩包", j22["total"] == 3 and s22["state"] == "done" and s22["done"] == 3 and s22["stage"] == "zip" and zip22, str(s22))
    rep.check("导出任务:查询进度,不存在的任务号报 404", au13.export_status(j22["job"])["state"] == "done" and _raises2(lambda: au13.export_status("nope")))
    rep.check("导出任务:没选笔记时拒绝", _raises2(lambda: au13.export_start(au13.ExportReq(ids=[]))))

    for _k in ('i', 'n',):
        if _k in locals():
            setattr(env, _k, locals()[_k])
