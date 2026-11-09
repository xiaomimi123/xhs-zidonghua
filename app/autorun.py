"""一键批量生成:选好赛道、填好篇数,后台自己走完 采集 → 分类 → 拆解 → 写稿配图 → 质检 → 出成品。

状态保存在 autoruns 表里,监督线程每隔几秒推进一步;服务重启后自动接着跑。
每一步都只依赖数据库里的当前状态,不依赖内存,所以随时可以中断、恢复。
质检不达标的稿子自动重写一次(配图一起重画),仍不达标就标成「需检查」,不会悄悄丢掉也不会混在合格品里。"""
import json
from collections import Counter
import threading
import time

from . import llm, notes as nt, products, worker
from .db import conn, get_settings, q_all, q_exec, q_one

ACTIVE = ("profiling", "collecting", "tagging", "analyzing", "writing", "retrying")
STEPS = ["profiling", "collecting", "tagging", "analyzing", "writing", "retrying", "done"]
TICK = 5


# ---------- 关键词 ----------
def is_broad(w: str) -> bool:
    """只有一个食材/品类名、没有「做法、教程」这类意图的词,搜出来容易不相关。"""
    return 2 <= len(w) <= 6 and not nt.INTENT.search(w)


def kw_suggest(track: str, word: str) -> dict:
    """宽泛的词 → 更具体、带意图的搜索词。结果缓存 30 天;AI 不可用时用通用说法。"""
    row = q_one("SELECT data FROM kw_cache WHERE word=? AND track=? AND created > datetime('now','-30 days')", word, track)
    if row and isinstance(row["data"], dict) and "suggestions" in row["data"]:
        return row["data"]
    try:
        r = llm.suggest_keywords(track, word)
        q_exec("INSERT OR REPLACE INTO kw_cache(word,track,data) VALUES(?,?,?)", word, track, json.dumps(r, ensure_ascii=False))
        return r
    except Exception:
        return {**llm.fallback_keywords(track, word), "fallback": True}


def refine_keywords(track: str, kws: list[str], default_n: int = 3, cap: int = 5) -> list[str]:
    """没填关键词就取赛道预设的前几个;宽泛的词自动换成更具体的说法(词少时一个换两个)。最多 cap 个。"""
    kws = [k for k in dict.fromkeys(k.strip() for k in kws) if k]
    if not kws:
        row = q_one("SELECT keywords FROM tracks WHERE name=?", track)
        kws = list((row or {}).get("keywords") or [])[:default_n]
    per, out = (2 if len(kws) <= 2 else 1), []
    for w in kws:
        sug = []
        if is_broad(w):
            try:
                r = kw_suggest(track, w)
                sug = [x["word"] for x in r.get("suggestions", [])[:per]] if r.get("broad") else []
            except Exception:
                sug = []
        out += sug or [w]
    return list(dict.fromkeys(out))[:cap]


# ---------- 选参考笔记 ----------
def _eng(n: dict) -> int:
    return (n.get("likes") or 0) + (n.get("collects") or 0) + (n.get("comments") or 0)


def pick_groups(pid: int, s: dict) -> list[tuple[str, list[int]]]:
    """按主题分组,每组取互动量最高的几篇做参考。只用图文、和搜索词相关、发布时间在范围内的笔记;
    样本太少的主题不单独拆解,实在凑不出分组就用全部笔记。返回 [(主题, [笔记 id])]。"""
    rows = _project_notes(pid, s)
    ok = [n for n in rows if n.get("note_type") != "video" and n["rel"] == "high" and not n["old"]]
    nrefs, maxt = int(s.get("auto_refs") or 5), int(s.get("auto_max_topics") or 3)
    by: dict[str, list[dict]] = {}
    for n in ok:
        t = n.get("topic") or ""
        if t and t != "未识别":
            by.setdefault(t, []).append(n)
    used = used_counts()

    def pick(ns):  # 互动量前 3×N 篇里,优先用以前没当过参考(或用得少)的,让每批的拆解有变化
        return sorted(sorted(ns, key=_eng, reverse=True)[:nrefs * 3], key=lambda n: (used[n["id"]], -_eng(n)))[:nrefs]
    groups = [(t, pick(ns)) for t, ns in by.items() if len(ns) >= 3]
    groups.sort(key=lambda g: -sum(_eng(n) for n in g[1]))
    groups = groups[:maxt]
    if not groups:
        pool = ok if len(ok) >= 3 else ([n for n in rows if n.get("note_type") != "video"] or rows)
        top = pick(pool)
        groups = [("", top)] if top else []
    return [(t, [n["id"] for n in ns]) for t, ns in groups]


def used_counts() -> Counter:
    """每篇笔记以前被拿来当拆解参考的次数(所有拆解卡里出现的次数)。"""
    return Counter(i for r in q_all("SELECT note_ids FROM playbooks") for i in (r["note_ids"] or []))


def allocate(total: int, k: int) -> list[int]:
    """把篇数平均分给 k 组,余数给靠前的组。"""
    return [total // k + (1 if i < total % k else 0) for i in range(k)] if k else []


# ---------- 质检 ----------
def _missing_images(d: dict) -> bool:
    return bool(d["with_images"] and d["image_prompts"]) and len([i for i in d["images"] if i]) < len(d["image_prompts"])


def finished(d: dict, s: dict) -> bool:
    """这篇是不是已经处理完(写完、审完、画完,或者出错停下了)。"""
    if d["status"] == "error":
        return True
    reviewed = bool(d["review"]) or s.get("auto_review") != "1"
    if d["status"] == "ready":
        return reviewed
    # 没有配图提示词的稿子不会进入生图,写完审完就是终态
    return d["status"] == "text_done" and reviewed and (not d["with_images"] or not d["image_prompts"])


def judge(d: dict, s: dict) -> tuple[bool, str, str]:
    """返回 (是否合格, 原因, 不合格时的补救动作:redo 重写 / img 补图 / 空 不再处理)。"""
    c, r = d["checks"] or {}, d["review"] or {}
    if d["status"] == "error":
        if d["title"] and _missing_images(d):
            return False, "部分配图没生成成功", "img"
        return False, "生成失败:" + (d["error"] or "未知原因")[:60], "redo"
    if c.get("high"):
        return False, "仍有高风险词", "redo"
    if c.get("combo"):
        return False, "主题搭配不合理", "redo"
    score, need = r.get("overall"), int(s.get("auto_pass_score") or 0)
    if isinstance(score, (int, float)) and score < need:
        return False, f"审稿 {score} 分,低于合格线 {need}", "redo"
    if _missing_images(d):
        return False, "配图不全", "img"
    if c.get("placeholders"):
        return False, "有待补充的内容(【补充:…】)", ""
    return True, "", ""


def _redo(did: int):
    """整篇重写:配图和审稿结果一并清掉,按新的标题重画。"""
    q_exec("UPDATE drafts SET images='[]', review='{}', variant='{}', error='' WHERE id=?", did)
    worker.submit_text(did)


# ---------- 状态机 ----------
def task_opts(kind: str = "keyword") -> tuple:
    """新建采集任务时,把当前的采集条件(时间范围、笔记类型、排序)记在任务上。博主任务取代表作,不限时间。"""
    s = get_settings()
    age = 0 if kind == "author" else int(s.get("collect_max_age_days") or 0)
    return age, (s.get("collect_note_type") or "image"), (s.get("collect_sort") or "popularity")


def add_tasks(pid: int, kws: list[str]):
    lim = int(get_settings()["collect_limit"])
    for k in kws:
        q_exec("INSERT INTO crawl_tasks(project_id,type,query,lim,max_age_days,note_type,sort) VALUES(?,?,?,?,?,?,?)",
               pid, "keyword", k, lim, *task_opts())


def new_run(pid: int, target: int, status: str = "collecting", msg: str = "", **state):
    msg = msg or ("正在识别产品图" if status == "profiling" else "已创建采集任务,等待采集端领取")
    q_exec("INSERT OR REPLACE INTO autoruns(project_id,target,status,msg,state) VALUES(?,?,?,?,?)",
           pid, target, status, msg, json.dumps({"t_stage": time.time(), **state}))


def on_topic(kw: str, core: str) -> bool:
    """搜索词里带不带产品/主题的核心词(至少共享两个连续的字)。"""
    return not core or nt._shares(kw, core) or nt._shares(core, kw)


def _project_notes(pid: int, s: dict) -> list[dict]:
    """本项目的笔记(重复的只留代表),相关度按本项目的搜索词重新判断:
    从笔记库复用来的笔记,当初可能是被别的词采到的。"""
    age = int(s.get("collect_max_age_days") or 0)
    p = q_one("SELECT keywords FROM projects WHERE id=?", pid)
    kw = "、".join((p or {}).get("keywords") or [])
    out = []
    for r in q_all("SELECT n.* FROM project_notes pn JOIN notes n ON n.id=pn.note_id WHERE pn.project_id=? AND (n.dup_of IS NULL OR n.dup_of NOT IN (SELECT note_id FROM project_notes WHERE project_id=pn.project_id))", pid):
        if kw:
            r["keyword"] = kw
        out.append(nt.annotate(r, age))
    return out


def relevant_count(pid: int, s: dict) -> int:
    return sum(1 for n in _project_notes(pid, s) if n.get("note_type") != "video" and n["rel"] == "high" and not n["old"])


# ---------- 笔记库优先:库里已有足够的相关笔记就不再采集 ----------
def library_match(kws: list[str], s: dict, limit: int = 200) -> list[int]:
    """笔记库里和这些搜索词相关、发布时间在范围内、不是重复帖的图文笔记,按互动量从高到低。"""
    terms = list(dict.fromkeys(t for k in kws for t in nt.core_terms(k) if len(t) >= 2))
    if not terms:
        return []
    cond = " OR ".join("(title LIKE ? OR tags LIKE ? OR topic LIKE ?)" for _ in terms)
    rows = q_all(f"SELECT * FROM notes WHERE dup_of IS NULL AND note_type!='video' AND ({cond}) "
                 "ORDER BY likes+collects+comments DESC LIMIT 400", *[f"%{t}%" for t in terms for _ in range(3)])
    age, kw, out = int(s.get("collect_max_age_days") or 0), "、".join(kws), []
    for r in rows:
        r["keyword"] = kw
        nt.annotate(r, age)
        if r["rel"] == "high" and not r["old"]:
            out.append(r["id"])
    return out[:limit]


def source(pid: int, kws: list[str], s: dict | None = None) -> dict:
    """先从笔记库找相关笔记关联到项目;够数(auto_library_min)就不采集,不够才给采集端下任务。"""
    s = s or get_settings()
    ids = library_match(kws, s)
    with conn() as c:
        for i in ids:
            c.execute("INSERT OR IGNORE INTO project_notes(project_id,note_id) VALUES(?,?)", (pid, i))
    if len(ids) >= int(s.get("auto_library_min") or 15):
        return {"library": len(ids), "collect": False}
    add_tasks(pid, kws)
    return {"library": len(ids), "collect": True}


def source_msg(src: dict) -> str:
    if not src["collect"]:
        return f"笔记库已有 {src['library']} 篇相关笔记,跳过采集"
    return (f"笔记库已有 {src['library']} 篇相关笔记,再采一些新的" if src["library"] else "已创建采集任务") + ",等待采集端领取"


def _core_word(project: dict) -> str:
    prod = products.get(project.get("product_id"))
    if prod:
        return prod.get("category") or prod["name"]
    ks = project.get("keywords") or []
    return (nt.core_terms(ks[0]) or [""])[0] if ks else ""


def diversify(kws: list[str], per_core: int = 2) -> list[str]:
    """同一个核心词(去掉做法、推荐这类意图词后一样)的搜索词最多留 per_core 个:
    「金汤酱料推荐 / 做法 / 测评 / 食谱」搜出来的笔记高度重合,采了前两个,后面基本都是已有的。"""
    seen, out = {}, []
    for k in kws:
        c = (nt.core_terms(k) or [k])[0]
        seen[c] = seen.get(c, 0) + 1
        if seen[c] <= per_core:
            out.append(k)
    return out


def _with_intent(word: str, track: str) -> str:
    return word if nt.INTENT.search(word) else f"{word}{'做法' if track == '美食' else '推荐'}"


def build_keywords(prof: dict, name: str, track: str, cap: int = 8) -> list[str]:
    """产品的搜索词分三层:品类核心词(2 个,意图不同)+ 用这个产品能做的菜/用法(最多 3 个)+ 上位/相邻品类词(1 个)。
    只搜「品类+各种意图」,内容太窄、结果又重合,采到的笔记很少;往菜品、上位品类扩,才有足够多可以借鉴的笔记。"""
    cat = prof.get("category") or name
    core = diversify([k for k in (prof.get("keywords") or []) if on_topic(k, cat)], 2)[:2] or [f"{cat}做法", f"{cat}怎么选"]
    uses = [_with_intent(u, track) for u in (prof.get("uses") or [])[:3]]
    broad = [_with_intent(b, track) for b in (prof.get("broader") or [])[:1]]
    return diversify(list(dict.fromkeys(core + uses + broad)), 2)[:cap]


def topup_keywords(project: dict) -> list[str]:
    """相关笔记太少时补充的搜索词:先用产品资料里还没搜过的菜品/上位品类词,其次让 AI 给相邻的词,最后才是同一个词的其他说法。"""
    core, have = _core_word(project), set(project.get("keywords") or [])
    if not core:
        return []
    track = project["track"]
    prod = products.get(project.get("product_id"))
    prof = (prod or {}).get("profile") or {}
    adj = [_with_intent(w, track) for w in [*(prof.get("uses") or []), *(prof.get("broader") or [])]]
    if not [w for w in adj if w not in have]:
        try:
            adj = llm.expand_adjacent(core, track, 6, list(have))
        except Exception:
            adj = []
    try:
        sug = [x["word"] for x in kw_suggest(track, core).get("suggestions", [])]
    except Exception:
        sug = []
    sug += [f"{core}做法", f"{core}怎么吃", f"{core}怎么选"]
    near = [w for w in dict.fromkeys(sug) if w not in have and on_topic(w, core)][:2]
    return list(dict.fromkeys([w for w in adj if w not in have][:2] + near))[:3]


def _profile(run_id: int):
    """看产品图 → 定赛道、定搜索词、建采集任务。看图失败也不卡住:退回用产品名当搜索词。"""
    run = q_one("SELECT * FROM autoruns WHERE id=?", run_id)
    project = run and q_one("SELECT * FROM projects WHERE id=?", run["project_id"])
    prod = project and products.get(project["product_id"])
    if not prod:
        return q_exec("UPDATE autoruns SET status='error', msg='找不到产品资料' WHERE id=?", run_id)
    tracks = [t["name"] for t in q_all("SELECT name FROM tracks ORDER BY sort")]
    try:
        prof = llm.describe_product(prod["name"], products.paths(prod, 4), tracks)
    except Exception as e:
        prof = {"error": str(e)[:120]}
        try:  # 看图失败:只凭产品名判断品类和搜索词。直接拿产品名(往往是品牌名)去搜,小红书上几乎搜不到笔记
            prof = {**llm.describe_by_name(prod["name"], tracks), "vision_error": prof["error"]}
        except Exception:
            pass
    products.save_profile(prod["id"], prof)
    track = project["track"] or prof.get("track") or prof.get("category") or prod["name"]
    s = get_settings()
    cap = int(s.get("auto_max_keywords") or 8)
    kws = build_keywords(prof, prod["name"], track, cap) if prof.get("category") else [prod["name"]]
    kws = refine_keywords(track, kws, int(s.get("auto_default_keywords") or 3), cap)
    direction = project["direction"] or f"为「{prod['name']}」做种草笔记:自然植入产品,先讲读者关心的场景和问题,再带出产品"
    q_exec("UPDATE projects SET track=?, keywords=?, direction=? WHERE id=?", track, json.dumps(kws, ensure_ascii=False), direction, project["id"])
    src = source(project["id"], kws, s)
    note = (f"没能识别产品图,按产品名判断为「{prof['category']}」" if prof.get("by_name") else f"已识别为「{prof.get('category') or prod['name']}」") if not prof.get("error") else "没能识别产品图,改用产品名搜索"
    _save(q_one("SELECT * FROM autoruns WHERE id=?", run_id), "collecting", f"{note};{source_msg(src)}", library=src["library"])


def _save(run: dict, status: str | None = None, msg: str | None = None, **state):
    st = {**(run["state"] or {}), **state}
    if status and status != run["status"]:
        st["t_stage"] = time.time()
        if status in ("error", "canceled") and run["status"] in ACTIVE:
            st["failed_at"] = run["status"]  # 记住停在哪一步,页面上标出来
    q_exec("UPDATE autoruns SET status=?, msg=?, state=?, updated=CURRENT_TIMESTAMP WHERE id=?",
           status or run["status"], msg if msg is not None else run["msg"], json.dumps(st, ensure_ascii=False), run["id"])
    run["state"], run["status"] = st, status or run["status"]


def _waited_min(run: dict) -> float:
    return (time.time() - (run["state"] or {}).get("t_stage", time.time())) / 60


def advance(run: dict):
    s, pid, st = get_settings(), run["project_id"], run["state"] or {}
    status = run["status"]
    project = q_one("SELECT * FROM projects WHERE id=?", pid)
    if not project:
        return _save(run, "error", "项目已被删除")

    if status == "profiling":
        if time.time() - st.get("prof_t", 0) > 600:  # 没有在跑(第一次,或服务重启把线程带走了)就提交一次
            _save(run, msg="正在识别产品图", prof_t=time.time())
            worker._pool("text").submit(_profile, run["id"])
        return

    if status == "collecting":
        retried = st.get("retried") or []
        for t in q_all("SELECT id,error FROM crawl_tasks WHERE project_id=? AND status='failed'", pid):
            # 采集端偶尔读笔记失败(页面数据为空等):每个任务自动重试一次,已经收到的笔记不会丢,也不会重复
            if t["id"] not in retried and not (t["error"] or "").startswith(("已取消", "等待超时")):
                # 「最热」排序里大多是老笔记,全被时间范围筛掉了:重试时改按「最新」排序,才能采到近期的
                newsort = ", sort='latest'" if "超出时间范围" in (t["error"] or "") else ""
                q_exec(f"UPDATE crawl_tasks SET status='pending', error='', worker=''{newsort} WHERE id=?", t["id"])
                retried.append(t["id"])
                st["retried"] = retried
                _save(run, retried=retried)
        tasks = q_all("SELECT status FROM crawl_tasks WHERE project_id=?", pid)
        pending = sum(1 for t in tasks if t["status"] in ("pending", "running"))
        n = q_one("SELECT COUNT(*) c FROM project_notes WHERE project_id=?", pid)["c"]
        enough = int(s.get("auto_enough_relevant") or 0)
        if pending and enough and n >= enough and relevant_count(pid, s) >= enough:
            q_exec("UPDATE crawl_tasks SET status='failed', error='已有足够多的相关笔记,不用再采了' WHERE project_id=? AND status IN ('pending','running')", pid)
            pending = 0
        wait = int(s.get("auto_collect_wait_min") or 90)
        if pending and _waited_min(run) < wait:
            return _save(run, msg=f"采集中:{len(tasks) - pending}/{len(tasks)} 个任务完成,已收到 {n} 篇笔记")
        # 采集端跳过了笔记库里已有的笔记(不会回传),这里把和本项目关键词相关的已有笔记补关联上,拆解时才用得上
        with conn() as c:
            for i in library_match(project["keywords"] or [], s):
                c.execute("INSERT OR IGNORE INTO project_notes(project_id,note_id) VALUES(?,?)", (pid, i))
        n = q_one("SELECT COUNT(*) c FROM project_notes WHERE project_id=?", pid)["c"]
        need = int(s.get("auto_min_notes") or 5)
        if n < need:
            why = "采集端超时没有领取或没有回传" if pending else "采集到的笔记太少"
            return _save(run, "error", f"{why}(收到 {n} 篇,至少需要 {need} 篇)。请确认采集端在线,或换关键词再试")
        if pending:
            q_exec("UPDATE crawl_tasks SET status='failed', error='等待超时,已跳过' WHERE project_id=? AND status IN ('pending','running')", pid)
        worker.wake_tagger()
        return _save(run, "tagging", f"采集完成,共 {n} 篇笔记,正在分类")

    if status == "tagging":
        if s.get("auto_tag") == "1":
            untagged = q_one("SELECT COUNT(*) c FROM project_notes pn JOIN notes n ON n.id=pn.note_id "
                             "WHERE pn.project_id=? AND (n.topic='' OR n.topic IS NULL)", pid)["c"]
            if untagged and _waited_min(run) < 10:
                worker.wake_tagger()
                return _save(run, msg=f"正在给笔记分类,还剩 {untagged} 篇")
        rc, need = relevant_count(pid, s), int(s.get("auto_min_relevant") or 5)
        if rc < need and not st.get("topup"):  # 采回来的笔记大多和产品/关键词无关:换几个更具体的词再采一轮
            new = topup_keywords(project)
            if new:
                q_exec("UPDATE projects SET keywords=? WHERE id=?", json.dumps((project["keywords"] or []) + new, ensure_ascii=False), pid)
                add_tasks(pid, new)
                return _save(run, "collecting", f"相关笔记只有 {rc} 篇,补充搜索「{'、'.join(new)}」", topup=True)
        if rc < 3:
            return _save(run, "error", f"采到的笔记和产品/关键词基本不相关(相关的只有 {rc} 篇)。请换更具体的关键词,或检查采集端的搜索结果")
        used = used_counts()
        groups = pick_groups(pid, s)
        if not groups:
            return _save(run, "error", "没有可用来拆解的笔记")
        refs = [i for _, nids in groups for i in nids]
        reused = sum(1 for i in refs if used[i])
        ids = []
        for topic, nids in groups:
            cur = q_exec("INSERT INTO playbooks(project_id,name,note_ids) VALUES(?,?,?)", pid,
                         f"自动·{topic or project['track']}", json.dumps(nids))
            ids.append(cur.lastrowid)
            worker.submit_playbook(cur.lastrowid)
        note = f",其中 {reused} 篇以前当过参考" + (",建议补采新笔记" if reused == len(refs) else "") if reused else ""
        return _save(run, "analyzing", f"正在拆解爆款({len(ids)} 个主题,参考 {len(refs)} 篇{note})", pbs=ids, refs=len(refs), reused=reused)

    if status == "analyzing":
        pbs = q_all(f"SELECT id,status,error FROM playbooks WHERE id IN ({','.join('?' * len(st['pbs']))})", *st["pbs"]) if st.get("pbs") else []
        if any(p["status"] in ("queued", "running") for p in pbs) and _waited_min(run) < 30:
            return _save(run, msg=f"拆解中:{sum(1 for p in pbs if p['status'] == 'done')}/{len(pbs)} 张拆解卡完成")
        good = [p["id"] for p in pbs if p["status"] == "done"]
        if not good:
            return _save(run, "error", "拆解失败:" + "; ".join((p["error"] or "")[:60] for p in pbs))
        dids = []
        for pb_id, cnt in zip(good, allocate(run["target"], len(good))):
            if cnt:
                dids += worker.plan_and_submit(pid, pb_id, cnt, project["direction"] or "", s.get("auto_images") != "0")
        return _save(run, "writing", f"正在写稿和配图(共 {len(dids)} 篇)", drafts=dids)

    if status in ("writing", "retrying"):
        ids = st.get("drafts") or []
        ds = q_all(f"SELECT * FROM drafts WHERE id IN ({','.join('?' * len(ids))})", *ids) if ids else []
        pending = [d for d in ds if not finished(d, s)]
        timeout = _waited_min(run) >= int(s.get("auto_write_wait_min") or 60)
        if pending and not timeout:
            return _save(run, msg=f"{'质检重写' if status == 'retrying' else '写稿配图'}中:{len(ds) - len(pending)}/{len(ds)} 篇完成")
        tries, limit, changed = st.get("tries") or {}, int(s.get("auto_retry") or 0), False
        for d in ds:
            if d in pending:  # 超时还没出来的,标成需检查
                q_exec("UPDATE drafts SET auto_ok=0, auto_note='处理超时' WHERE id=?", d["id"])
                continue
            ok, why, act = judge(d, s)
            t = tries.setdefault(str(d["id"]), {"redo": 0, "img": 0})
            if ok:
                q_exec("UPDATE drafts SET auto_ok=1, auto_note='' WHERE id=?", d["id"])
            elif act == "img" and t["img"] < 2:
                t["img"] += 1; changed = True
                worker.submit_images(d["id"])
            elif act == "redo" and t["redo"] < limit:
                t["redo"] += 1; changed = True
                _redo(d["id"])
            else:
                q_exec("UPDATE drafts SET auto_ok=0, auto_note=? WHERE id=?", why, d["id"])
        if changed:
            return _save(run, "retrying", "质检:不合格的稿子正在自动重写", tries=tries)
        passed = q_one(f"SELECT COUNT(*) c FROM drafts WHERE auto_ok=1 AND id IN ({','.join('?' * len(ids))})", *ids)["c"] if ids else 0
        return _save(run, "done", f"完成:{passed} 篇合格,{len(ds) - passed} 篇需要检查", tries=tries)


def tick():
    try:
        from . import qianfan
        qianfan.schedule()
    except Exception:
        pass
    try:
        from . import loop
        loop.auto_tick()
    except Exception:
        pass
    for run in q_all(f"SELECT * FROM autoruns WHERE status IN ({','.join('?' * len(ACTIVE))})", *ACTIVE):
        try:
            advance(run)
        except Exception as e:  # 一个项目出错不影响其他项目;下一轮重试
            q_exec("UPDATE autoruns SET msg=? WHERE id=?", f"处理中遇到问题,稍后自动重试:{str(e)[:80]}", run["id"])


def _loop():
    while True:
        time.sleep(TICK)
        try:
            tick()
        except Exception:
            pass


def start():
    threading.Thread(target=_loop, daemon=True, name="autorun").start()


def cancel(pid: int):
    run = q_one("SELECT * FROM autoruns WHERE project_id=?", pid)
    if run and run["status"] in ACTIVE:
        _save(run, "canceled", "已取消")
    q_exec("UPDATE crawl_tasks SET status='failed', error='已取消' WHERE project_id=? AND status IN ('pending','running')", pid)


def progress(pid: int) -> dict | None:
    run = q_one("SELECT * FROM autoruns WHERE project_id=?", pid)
    if not run:
        return None
    st = run["state"] or {}
    tasks = q_all("SELECT status FROM crawl_tasks WHERE project_id=?", pid)
    ids = st.get("drafts") or []
    ds = q_all(f"SELECT status,auto_ok,title FROM drafts WHERE id IN ({','.join('?' * len(ids))})", *ids) if ids else []
    pbs = q_all(f"SELECT status FROM playbooks WHERE id IN ({','.join('?' * len(st['pbs']))})", *st["pbs"]) if st.get("pbs") else []
    pr = q_one("SELECT product_id FROM projects WHERE id=?", pid)
    return {"status": run["status"], "msg": run["msg"], "target": run["target"], "active": run["status"] in ACTIVE,
            "product": bool(pr and pr["product_id"]), "failed_at": st.get("failed_at", ""),
            "library": st.get("library", 0), "refs": st.get("refs", 0), "reused": st.get("reused", 0),
            "step": STEPS.index(run["status"]) if run["status"] in STEPS else -1,
            "tasks": [sum(1 for t in tasks if t["status"] not in ("pending", "running")), len(tasks)],
            "notes": q_one("SELECT COUNT(*) c FROM project_notes WHERE project_id=?", pid)["c"],
            "playbooks": [sum(1 for p in pbs if p["status"] == "done"), len(pbs)],
            "drafts": {"total": len(ds), "passed": sum(1 for d in ds if d["auto_ok"] == 1),
                       "finished": sum(1 for d in ds if d["status"] in ("ready", "text_done", "exported", "error") and d["title"]),
                       "failed": sum(1 for d in ds if d["auto_ok"] == 0)}}
