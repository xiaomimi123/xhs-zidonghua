"""后台任务:拆解、写文案、生图、帖子打标签。
任务状态都在数据库里(playbooks / drafts / notes 的 status 字段),服务重启后 resume() 会接着跑。"""
import json
import shutil
import re
import threading
import time
from itertools import combinations
from concurrent.futures import ThreadPoolExecutor
from . import thumbs
from . import llm, imagegen, compliance, stats, review, mirror, lengthcap, products, cards, covers
from .db import conn, row_to_dict, get_settings

_lock = threading.Lock()
_pools: dict[str, tuple[int, ThreadPoolExecutor]] = {}


def _pool(kind: str) -> ThreadPoolExecutor:
    """按设置里的并发数取线程池;并发数改了就换新池(旧池跑完手头任务后自动回收)。"""
    n = max(1, int(get_settings().get(f"{kind}_concurrency") or 1))
    with _lock:
        cur = _pools.get(kind)
        if not cur or cur[0] != n:
            if cur:
                cur[1].shutdown(wait=False)
            _pools[kind] = (n, ThreadPoolExecutor(n, thread_name_prefix=kind))
        return _pools[kind][1]


def _one(sql, *a):
    with conn() as c:
        r = c.execute(sql, a).fetchone()
    return row_to_dict(r) if r else None


def _exec(sql, *a):
    with conn() as c:
        c.execute(sql, a)


def _notes(ids):
    if not ids:
        return []
    with conn() as c:
        rows = c.execute(f"SELECT * FROM notes WHERE id IN ({','.join('?' * len(ids))}) ORDER BY likes DESC", ids)
        return [row_to_dict(r) for r in rows]


def knowledge(track: str) -> str:
    """按需加载:只带通用规则 + 当前赛道那一份知识库。"""
    row = _one("SELECT knowledge FROM tracks WHERE name=?", track or "")
    return "\n\n".join(x for x in (get_settings().get("kb_common", ""), (row or {}).get("knowledge", "")) if x and x.strip())


def _ref_texts(refs: list[dict]) -> list[str]:
    return [(r.get("title") or "") + (r.get("content") or "") for r in refs]


def forced_pairs(keywords: list[str], text: str, ref_texts: list[str]) -> list[tuple[str, str]]:
    """文字里同时出现了两个关键词,而参考笔记里没有任何一篇同时提到它们 = 被强行组合在一起了。"""
    return [(a, b) for a, b in combinations(keywords or [], 2)
            if a in text and b in text and not any(a in t and b in t for t in ref_texts)]


def _analyze_checked(project: dict, refs: list[dict], st: dict) -> dict:
    """拆解后检查名称和总结有没有把不相关的关键词糊在一起;有就带着反馈重做一次,还不行就换成通用名称。"""
    args = (project, refs, stats.to_prompt(st), knowledge(project["track"]))
    data, rt = llm.analyze(*args), _ref_texts(refs)
    text = lambda d: f"{d.get('name', '')} {d.get('summary', '')}"
    bad = forced_pairs(project["keywords"], text(data), rt)
    if bad:
        data = llm.analyze(*args, feedback=f"上一版把「{bad[0][0]}」和「{bad[0][1]}」放在了一起,但参考笔记里并没有这种搭配。"
                                           "名称和总结只能围绕单个主题,或用「写法」「套路」这类通用说法。")
        if forced_pairs(project["keywords"], text(data), rt):
            data["name"] = f"{project['track']}写法拆解"
    return data


# ---------- 拆解 ----------
def submit_playbook(pb_id: int):
    _pool("text").submit(_run_playbook, pb_id)


def _run_playbook(pb_id):
    pb = _one("SELECT * FROM playbooks WHERE id=?", pb_id)
    if not pb:
        return
    _exec("UPDATE playbooks SET status='running', error='' WHERE id=?", pb_id)
    try:
        project = _one("SELECT * FROM projects WHERE id=?", pb["project_id"])
        refs = _notes(pb["note_ids"])
        st = stats.build(project["track"], refs)
        _exec("UPDATE playbooks SET stats=? WHERE id=?", json.dumps(st, ensure_ascii=False), pb_id)
        data = _analyze_checked(project, refs, st)
        _exec("UPDATE playbooks SET status='done', name=?, data=? WHERE id=?",
              data.get("name") or pb["name"], json.dumps(data, ensure_ascii=False), pb_id)
    except Exception as e:
        _exec("UPDATE playbooks SET status='error', error=? WHERE id=?", str(e), pb_id)


# ---------- 复刻 ----------
def plan_and_submit(project_id: int, playbook_id: int, count: int, extra: str, with_images: bool) -> list[int]:
    """先建好草稿占位(页面立即可见),再在后台规划角度、逐篇写作。"""
    with conn() as c:
        pj = c.execute("SELECT account_id, account_name FROM projects WHERE id=?", (project_id,)).fetchone()
        acc = (pj["account_id"] or "", pj["account_name"] or "") if pj else ("", "")   # 任务指定了发布账号,稿子继承它
        ids = [c.execute("INSERT INTO drafts(project_id,playbook_id,extra,with_images,status,account_id,account_name) VALUES(?,?,?,?,'queued',?,?)",
                         (project_id, playbook_id, extra, int(with_images), *acc)).lastrowid for _ in range(count)]
    _pool("text").submit(_plan, project_id, playbook_id, ids, extra)
    return ids


def _plan(project_id, playbook_id, ids, extra):
    try:
        project = _one("SELECT * FROM projects WHERE id=?", project_id)
        pb = _one("SELECT * FROM playbooks WHERE id=?", playbook_id)
        angles = llm.plan_angles(project, pb["data"], len(ids), extra + products.block(products.get(project.get("product_id"))))
    except Exception:
        angles = [""] * len(ids)  # 规划失败不阻塞,各篇自由发挥
    for did, a in zip(ids, angles):
        _exec("UPDATE drafts SET angle=? WHERE id=?", a, did)
        submit_text(did)


def submit_text(did: int):
    _exec("UPDATE drafts SET status='queued', error='' WHERE id=?", did)
    _pool("text").submit(_run_text, did)


def _run_text(did):
    d = _one("SELECT * FROM drafts WHERE id=?", did)
    if not d:
        return
    _exec("UPDATE drafts SET status='writing' WHERE id=?", did)
    try:
        project = _one("SELECT * FROM projects WHERE id=?", d["project_id"])
        pb = _one("SELECT * FROM playbooks WHERE id=?", d["playbook_id"])
        if not pb:
            raise RuntimeError("拆解卡已被删除")
        refs, kb, banned = _notes(pb["note_ids"]), knowledge(project["track"]), compliance.banned_for_prompt()
        prod = products.get(project.get("product_id"))
        n_img = covers.image_count(get_settings())  # 这篇一共几张图(含封面),随机 1~4
        pblock = products.block(prod) + image_block(n_img)
        if prod and prod.get("forbid"):
            banned += f"\n- 作者禁用:{prod['forbid']}"
        out = llm.write_note(project, pb["data"], refs, d["angle"], d["extra"] + pblock, kb, banned)
        rt = _ref_texts(refs)
        bad = forced_pairs(project["keywords"], f"{out.get('title', '')}{out.get('content', '')}", rt)
        if bad:  # 把不相关的词写进了同一篇:带着反馈重写一次
            hint = (f"上一版把「{bad[0][0]}」和「{bad[0][1]}」写在了同一篇里,参考笔记里没有这种搭配,现实中也不合常理。"
                    "这篇只写角度里点明的那一个主题,另一个不要出现,重新写。")
            out = llm.write_note(project, pb["data"], refs, d["angle"], f"{d['extra']} {hint}{pblock}".strip(), kb, banned)
        combo = forced_pairs(project["keywords"], f"{out.get('title', '')}{out.get('content', '')}", rt)
    except Exception as e:
        _exec("UPDATE drafts SET status='error', error=? WHERE id=?", f"写文案失败:{e}", did)
        return
    out.setdefault("title", ""); out.setdefault("content", ""); out.setdefault("alt_titles", []); out.setdefault("tags", [])
    prompts, cc = covers.plan(did, out, project["track"], prod["name"] if prod else project["track"], n_img, _safe_text)
    out["image_prompts"], out["cover_card"] = prompts, cc
    out, variant = _maybe_compress(out, pb["stats"], d["angle"], get_settings().get("auto_compress") == "1")
    out, checks = _check_and_fix(out, auto=get_settings().get("auto_fix") == "1")
    checks["combo"] = [list(p) for p in combo]  # 重写后仍然强行组合的,留给页面提示作者
    auto_review = get_settings().get("auto_review") == "1"
    _exec("UPDATE drafts SET title=?,alt_titles=?,content=?,tags=?,image_prompts=?,cover_card=?,checks=?,variant=?,status=?,error='',review='{}' WHERE id=?",
          out["title"], json.dumps(out["alt_titles"], ensure_ascii=False), out["content"],
          json.dumps(out["tags"], ensure_ascii=False), json.dumps(out.get("image_prompts", []), ensure_ascii=False),
          json.dumps(out.get("cover_card") or {}, ensure_ascii=False), json.dumps(checks, ensure_ascii=False), json.dumps(variant, ensure_ascii=False), "reviewing" if auto_review else "text_done", did)
    if auto_review:
        _run_review(did, "text_done")
    if d["with_images"]:
        submit_images(did)


def image_block(n: int) -> str:
    """告诉写作模型这篇一共几张图:第 1 张是程序排版的封面(只填 cover),其余是内页实拍图。"""
    return (f"\n\n【配图数量】本篇配图共 {n} 张:第 1 张是程序排版的封面,你只需要填 cover 字段;"
            + (f"其余 {n - 1} 张是内页实拍图,image_prompts 正好写 {n - 1} 条。" if n > 1 else "本篇只有封面,没有内页图,image_prompts 给空数组。"))


def _safe_text(t: str) -> str:
    """封面文字过一遍高风险词:有替换建议就换,没有就删掉那个词。"""
    for h in compliance.scan("", t, [])["hits"]:
        if h["severity"] == "high":
            rep = h["replacement"] or ""
            t = t.replace(h["word"], rep if 0 < len(rep) <= 8 and not rep.startswith(("删", "改成")) else "")
    return t.strip(" ,，。")


def _verify_hits(checks: dict) -> list[dict]:
    """把「需确认经历」的句子包装成修改请求,交给 revise 一起处理。"""
    return [{"category": "需确认经历", "word": v["text"][:40], "field": v["field"], "severity": "high",
             "replacement": "改成【补充:你的真实经历】占位符,或改写成不含「我」的通用方法建议"} for v in checks.get("verify", [])]


def _maybe_compress(out: dict, stats: dict, angle: str, auto: bool) -> tuple[dict, dict]:
    """正文明显超过爆款组字数(且基准可信)时压缩一次。成功:out 换成压缩版,variant 里留着原稿;不需要或失败:原样返回。"""
    p = lengthcap.plan(stats, angle, out["content"]) if auto else None
    if not p:
        return out, {}
    try:
        short = llm.compress(out, p["target"])
    except Exception:
        return out, {}
    holes = [m.group(1).strip() for m in compliance.PLACEHOLDER.finditer(out["content"])]
    new_holes = [m.group(1).strip() for m in compliance.PLACEHOLDER.finditer(short)]
    if not lengthcap.accept(out["content"], short, holes, new_holes):
        return out, {}
    variant = {"other": out["content"], "showing": "short", "short_len": len(short), "long_len": len(out["content"]),
               "baseline": p["baseline"], "cap": p["cap"], "basis": p["basis"], "still_long": len(short) > p["cap"]}
    return {**out, "content": short}, variant


def _check_and_fix(out: dict, auto: bool) -> tuple[dict, dict]:
    """扫描;有高风险命中或第一人称的亲身经历,且允许自动修时,让模型按扫描结果改一次再复扫。
    功效宣称、配图文字里的问题只提示,不自动改。"""
    prompts = out.get("image_prompts")
    checks = compliance.scan(out["title"], out["content"], out["tags"], prompts)
    checks["fixed"] = 0
    fixable = [h for h in checks["hits"] if h["field"] != "image_text" and h["category"] != "功效宣称"] + _verify_hits(checks)
    if auto and any(h["severity"] == "high" for h in fixable):
        try:
            fixed = llm.revise(out, fixable)
            for k in ("title", "content", "alt_titles", "tags"):
                if fixed.get(k):
                    out[k] = fixed[k]
            before = checks["high"] + checks["low"] + len(checks.get("verify", []))
            checks = compliance.scan(out["title"], out["content"], out["tags"], prompts)
            checks["fixed"] = max(0, before - checks["high"] - checks["low"] - len(checks.get("verify", [])))
        except Exception as e:
            checks["fix_error"] = str(e)[:200]
    return out, checks


def submit_fix(did: int):
    """用户手动点「按建议修改」:对当前稿子(含用户编辑)扫描并修一次。"""
    d = _one("SELECT * FROM drafts WHERE id=?", did)
    if not d:
        return
    _exec("UPDATE drafts SET status='writing' WHERE id=?", did)
    _pool("text").submit(_run_fix, did, d["status"])


def _run_fix(did, prev_status):
    d = _one("SELECT * FROM drafts WHERE id=?", did)
    out = {k: d[k] for k in ("title", "content", "alt_titles", "tags")}
    prompts = d["image_prompts"]
    checks = compliance.scan(out["title"], out["content"], out["tags"], prompts)
    hits = [h for h in checks["hits"] if h["field"] != "image_text"] + _verify_hits(checks)
    if hits:
        try:
            fixed = llm.revise(out, hits)
            for k in out:
                if fixed.get(k):
                    out[k] = fixed[k]
        except Exception as e:
            _exec("UPDATE drafts SET status=?, error=? WHERE id=?", prev_status, f"修改失败:{e}", did)
            return
    new = compliance.scan(out["title"], out["content"], out["tags"], prompts)
    new["fixed"] = max(0, len(checks["hits"]) + len(checks.get("verify", [])) - len(new["hits"]) - len(new.get("verify", [])))
    _exec("UPDATE drafts SET title=?,alt_titles=?,content=?,tags=?,checks=?,status=?,error='' WHERE id=?",
          out["title"], json.dumps(out["alt_titles"], ensure_ascii=False), out["content"],
          json.dumps(out["tags"], ensure_ascii=False), json.dumps(new, ensure_ascii=False),
          prev_status if prev_status in ("ready", "text_done", "exported") else "text_done", did)


# ---------- 审稿 ----------
def submit_review(did: int):
    d = _one("SELECT status FROM drafts WHERE id=?", did)
    if not d:
        return
    prev = d["status"] if d["status"] in ("ready", "text_done", "exported") else "text_done"
    _exec("UPDATE drafts SET status='reviewing' WHERE id=?", did)
    _pool("text").submit(_run_review, did, prev)


def _run_review(did, prev_status):
    """审稿失败不影响稿子本身,只在 review 里记录错误。"""
    d = _one("SELECT * FROM drafts WHERE id=?", did)
    if not d:
        return
    try:
        project = _one("SELECT * FROM projects WHERE id=?", d["project_id"])
        pb = _one("SELECT * FROM playbooks WHERE id=?", d["playbook_id"])
        if not pb:
            raise RuntimeError("拆解卡已被删除,无法对照审稿")
        r = review.run(project, pb["data"], pb["stats"], d, knowledge(project["track"]),
                       get_settings().get("review_comments") == "1")
    except Exception as e:
        r = {"error": str(e)[:300]}
    _exec("UPDATE drafts SET review=?, status=? WHERE id=?", json.dumps(r, ensure_ascii=False), prev_status, did)


def submit_images(did: int, redo_all: bool = False):
    """生图。默认只补还没有的图(不重复花钱);redo_all=True 才全部重画。"""
    _exec("UPDATE drafts SET status='queued', error='' WHERE id=?", did)
    _pool("image").submit(_run_images, did, redo_all)


def replan_images(did: int):
    """按正文重新策划内页提示词并全部重画(封面保留,不花钱;内页会重新扣生图费用)。"""
    d = _one("SELECT * FROM drafts WHERE id=?", did)
    k = len(d["image_prompts"]) - 1
    if k < 1:
        raise ValueError("这篇没有内页配图")
    pr = _one("SELECT product_id, track FROM projects WHERE id=?", d["project_id"])
    prod = products.get(pr["product_id"]) if pr else None
    raw = llm.inner_image_prompts(d["title"], d["content"], k, (prod or {}).get("name", ""))
    new = covers.inner_prompts(raw, k, pr["track"] if pr else "", did, d["title"])
    ps = [d["image_prompts"][0], *new]
    names = list(d["images"])
    if names:
        names = names[:1] + [""] * k  # 封面保留,内页清空后重画
    _exec("UPDATE drafts SET image_prompts=?, images=? WHERE id=?", json.dumps(ps, ensure_ascii=False), json.dumps(names), did)
    submit_images(did)


def _image_prompt(title: str, p: str, with_product: bool = False, scene: str = "") -> str:
    """给生图提示词加保险:不把标题塞进去(否则模型会把标题画成海报字并自编图标),并限定图里能出现的文字(防止编出英文口号或功效说法)。
    有产品参考图时,要求保持产品外观和包装不变。"""
    tail = "图中的文字只能是引号里的这些简体中文,不要添加任何其他文字。" if compliance.image_texts([p]) else "画面中不要出现任何文字、字母或水印。"
    head = "以参考图中的产品为主体,严格保持产品的外观、颜色、包装和包装上的文字不变。" if with_product else ""
    return f"{head}{f'场景:{scene}。' if scene else ''}{p.rstrip('。')}。{covers.UGC_STYLE}。不要标题大字、图标、对错符号、箭头、序号或标注。{tail}"


def _render_cover(did: int, cc: dict, refs: list) -> str:
    """渲染封面卡片。需要实拍背景的版式:用已有的实拍图,没有才生成一张(生成过的会记在 cc["photo"],换版式不再花钱)。"""
    photo = None
    if cards.LAYOUTS[cc["layout"]][1]:
        if cc.get("photo") and (imagegen.IMG_DIR / cc["photo"]).is_file():
            photo = imagegen.IMG_DIR / cc["photo"]
        else:
            cc["photo"] = imagegen.generate_image(covers.photo_prompt(cc, bool(refs)), f"d{did}_0_{int(time.time())}", **({"refs": refs} if refs else {}))
            photo = imagegen.IMG_DIR / cc["photo"]
    out = imagegen.IMG_DIR / f"d{did}_1_{int(time.time())}.png"
    cards.render(cc["layout"], cc.get("theme") or "cream", cc, out, photo)
    thumbs.warm(out)
    return out.name


def change_cover(did: int):
    """换个封面样式:换版式和配色,只重做第 1 张(有现成实拍图就不再花钱)。"""
    d = _one("SELECT * FROM drafts WHERE id=?", did)
    cc = d["cover_card"] or {}
    has_photo = bool(cc.get("photo")) and (imagegen.IMG_DIR / cc["photo"]).is_file()
    cc["layout"], cc["theme"] = covers.next_style(cc, has_photo)
    prompts, names = list(d["image_prompts"]), list(d["images"])
    if prompts:
        prompts[0] = covers.cover_line(cc)
    if names:
        names[0] = ""
    _exec("UPDATE drafts SET cover_card=?, image_prompts=?, images=? WHERE id=?", json.dumps(cc, ensure_ascii=False),
          json.dumps(prompts, ensure_ascii=False), json.dumps(names), did)
    submit_images(did)


def _run_images(did, redo_all=False):
    d = _one("SELECT * FROM drafts WHERE id=?", did)
    if not d:
        return
    _exec("UPDATE drafts SET status='drawing' WHERE id=?", did)
    prompts = d["image_prompts"]
    pr = _one("SELECT product_id, track FROM projects WHERE id=?", d["project_id"])
    prod = products.get(pr["product_id"]) if pr else None
    cc = d["cover_card"] or {}
    ppaths = products.paths(prod) if prod else []
    st_ = get_settings()
    refs = ppaths[:max(1, int(st_.get("product_max_refs") or 3))]
    real = min(int(st_.get("product_real_slots") or 0), max(0, len(prompts) - 1), len(ppaths))  # 最后几张直接用产品原图(封面始终 AI 生成)
    first_real = len(prompts) - real
    # 图片列表和提示词按位置一一对应,没生成成功的位置是空串;旧数据长度对不上就当作都还没生成
    names = [""] * len(prompts) if redo_all or len(d["images"]) != len(prompts) else list(d["images"])
    errs = []
    for i, p in enumerate(prompts):
        if names[i] and (imagegen.IMG_DIR / names[i]).is_file():
            continue
        names[i] = ""
        if real and i >= first_real:  # 产品原图:不花钱,包装和实物完全一致
            src = ppaths[i - first_real]
            dst = imagegen.IMG_DIR / f"d{did}_{i + 1}_{int(time.time())}{src.suffix}"
            shutil.copy2(src, dst)
            names[i] = dst.name
            _exec("UPDATE drafts SET images=? WHERE id=?", json.dumps(names), did)
            continue
        if i == 0 and cc.get("layout"):  # 程序排版的封面:文字不让 AI 画,需要实拍背景的版式只生成一张无字实拍图
            try:
                names[0] = _render_cover(did, cc, refs)
            except Exception as e:
                errs.append(f"第1张(封面):{e}")
            _exec("UPDATE drafts SET images=?, cover_card=? WHERE id=?", json.dumps(names), json.dumps(cc, ensure_ascii=False), did)
            continue
        try:
            names[i] = imagegen.generate_image(_image_prompt(d["title"], p, bool(refs), covers.scene(pr["track"] if pr else "", did, i) if cc.get("layout") else ""), f"d{did}_{i + 1}_{int(time.time())}", **({"refs": refs} if refs else {}))
        except Exception as e:
            errs.append(f"第{i + 1}张:{e}")
        _exec("UPDATE drafts SET images=? WHERE id=?", json.dumps(names), did)  # 出一张显示一张
    if errs:
        _exec("UPDATE drafts SET status='error', error=? WHERE id=?",
              f"{len(errs)} 张图没生成成功,点「补全缺失的图」只重试这几张(已成功的不会重复扣费)。" + ";".join(errs)[:400], did)
    else:
        _exec("UPDATE drafts SET status='ready', error='' WHERE id=?", did)


# ---------- 帖子自动打标签 ----------
_tag_wake = threading.Event()


def wake_tagger():
    _tag_wake.set()


def _tag_batch() -> bool:
    """处理一批(最多 10 篇)待打标签的笔记。返回 True 表示还可以继续取下一批,False 表示该停了(没有待办、没开、没配 Key 或配置有问题)。"""
    s = get_settings()
    if s.get("auto_tag") != "1" or not s.get("deepseek_api_key"):
        return False
    with conn() as c:
        rows = [dict(r) for r in c.execute("SELECT id,title,content,keyword FROM notes WHERE ai_status='pending' ORDER BY id LIMIT 10")]
        if not rows:
            return False
        c.executemany("UPDATE notes SET ai_status='running' WHERE id=?", [(r["id"],) for r in rows])
        # 把已有的主题带给模型,同一件东西沿用同一个写法,免得「姜黄粉」「黄姜粉」被当成两个主题
        known = [r[0] for r in c.execute("SELECT topic FROM notes WHERE topic!='' AND topic!='未识别' GROUP BY topic ORDER BY COUNT(*) DESC LIMIT 40")]
        ids = [r["id"] for r in rows]
        terms = []  # 这些笔记所属项目的搜索词:让模型知道「采集时在找什么」,好判断哪些笔记其实无关
        for (kw,) in c.execute("SELECT p.keywords FROM project_notes pn JOIN projects p ON p.id=pn.project_id "
                               f"WHERE pn.note_id IN ({','.join('?' * len(ids))})", ids):
            terms += json.loads(kw or "[]")
        terms += [r["keyword"] for r in rows if r.get("keyword") and not r["keyword"].startswith("博主:")]  # 笔记库沉淀的笔记没有项目:用采集它时的搜索词
        terms = list(dict.fromkeys(terms))
    try:
        res = llm.tag_notes(rows, known, terms)
    except Exception as e:
        with conn() as c:
            c.executemany("UPDATE notes SET ai_status='error', ai_error=? WHERE id=?", [(str(e)[:300], r["id"]) for r in rows])
        return not ("Key" in str(e) or "401" in str(e))  # 配置问题就不再空转
    with conn() as c:
        for r in rows:
            t = res.get(r["id"])
            if t:
                c.execute("UPDATE notes SET ai_status='done', ai_type=?, topic=?, ai_audience=?, ai_highlight=?, ai_tags=?,"
                          " ai_error='' WHERE id=?", (t["type"], t["topic"], t["audience"], t["highlight"],
                                                      json.dumps(t["tags"], ensure_ascii=False), r["id"]))
            else:
                c.execute("UPDATE notes SET ai_status='error', ai_error='模型未返回该篇结果' WHERE id=?", (r["id"],))
    return True


def _track_batch() -> bool:
    """给没有赛道的笔记(笔记库沉淀的、导入时没选赛道的)补赛道:从赛道列表里让模型选最合适的,都不合适记「未分类」。
    返回 True 表示还可以继续取下一批。"""
    s = get_settings()
    if s.get("auto_tag") != "1" or not s.get("deepseek_api_key"):
        return False
    with conn() as c:
        rows = [dict(r) for r in c.execute("SELECT id,title,content,topic FROM notes WHERE (track='' OR track IS NULL) AND ai_status='done' ORDER BY id LIMIT 15")]
        names = [r[0] for r in c.execute("SELECT name FROM tracks WHERE enabled=1 ORDER BY sort, id")]
    if not rows or not names:
        return False
    try:
        res = llm.classify_tracks(rows, names)
    except Exception as e:
        return not ("Key" in str(e) or "401" in str(e)) and False  # 出错就停,下次唤醒再试,不空转
    with conn() as c:
        c.executemany("UPDATE notes SET track=? WHERE id=?", [(res.get(r["id"], "未分类"), r["id"]) for r in rows])
    return True


def _tagger_loop():
    """常驻线程:有待打标签的笔记就一批一批处理。没配 Key 或关了自动打标签就等着。"""
    while True:
        _tag_wake.wait(timeout=30)
        _tag_wake.clear()
        try:
            while _tag_batch():
                pass
            while _track_batch():
                pass
        except Exception:
            time.sleep(5)


# ---------- 启动恢复 ----------
def resume():
    """服务启动时把上次没跑完的拆解、复刻任务重新放进队列,并启动打标签线程。"""
    with conn() as c:
        pbs = [r[0] for r in c.execute("SELECT id FROM playbooks WHERE status IN ('queued','running')")]
        drafts = [dict(r) for r in c.execute(
            "SELECT id, title, image_prompts, with_images, status FROM drafts WHERE status IN ('queued','writing','drawing','reviewing')")]
    for pid in pbs:
        submit_playbook(pid)
    for d in drafts:
        if d["status"] == "reviewing":
            _exec("UPDATE drafts SET status='text_done' WHERE id=?", d["id"])
            submit_review(d["id"])
        elif d["title"] and d["with_images"] and d["image_prompts"] not in ("", "[]"):
            submit_images(d["id"])
        else:
            submit_text(d["id"])
    threading.Thread(target=_tagger_loop, daemon=True, name="tagger").start()
    wake_tagger()
    mirror.start()  # 接着转存上次没下完的图片
