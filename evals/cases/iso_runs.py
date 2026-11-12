"""任务页删除:进行中的拒绝,完成的连采集任务、稿子一起删,笔记库不动。"""
import json

from ..common import _raises2


def run(rep, env):
    from app import api_user, db

    q = env.q
    print("\n[删除任务]")
    pj = db.q_exec("INSERT INTO projects(track,keywords) VALUES('美食',?)", json.dumps([])).lastrowid
    db.q_exec("INSERT INTO autoruns(project_id,target,status) VALUES(?,3,'collecting')", pj)
    db.q_exec("INSERT INTO drafts(keyword,title,project_id) VALUES('x','删除测试稿',?)", pj)
    rep.check("删除任务:进行中的拒绝", _raises2(lambda: api_user.delete_run(pj)) and q("SELECT COUNT(*) c FROM projects WHERE id=?", pj)[0]["c"] == 1)
    db.q_exec("UPDATE autoruns SET status='done' WHERE project_id=?", pj)
    api_user.delete_run(pj)
    gone = all(q(f"SELECT COUNT(*) c FROM {t} WHERE {k}=?", pj)[0]["c"] == 0 for t, k in (("projects", "id"), ("drafts", "project_id"), ("autoruns", "project_id")))
    rep.check("删除任务:完成的连任务、稿子一起删", gone)
    rep.check("删除任务:不存在的返回 404", _raises2(lambda: api_user.delete_run(99999)))
