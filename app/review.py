"""复刻后审稿:代码算量化贴合度和合规扣分,模型给各维度分、问题清单和修改版,最终等级由代码合成。
总分 = 模型六个维度均分 × 75% + 量化贴合度 × 25% − 高风险词 8 分/个。"""
from statistics import mean
from . import compliance, llm, stats

# (指标, 名称, 比较方式, 容差):rel=相对偏差,abs=绝对差,bool=参考组过半有则稿子也要有
BENCH = [
    ("title_len", "标题字数", "rel", 0.3),
    ("body_len", "正文字数", "rel", 0.4),
    ("paras", "段落数", "rel", 0.4),
    ("emoji_100", "每百字 emoji", "abs", 1.5),
    ("list_ratio", "分点段落占比", "abs", 0.2),
    ("cta", "结尾互动引导", "bool", None),
    ("tags", "话题数", "abs", 2),
]
GRADES = [(90, "S"), (80, "A"), (70, "B"), (60, "C"), (0, "D")]


def _fmt(k, v):
    return f"{v:.0%}" if k == "list_ratio" else ("有" if v else "无") if k == "cta" else f"{v:.1f}" if k == "emoji_100" else f"{v:.0f}"


def quant(draft: dict, ref: dict | None) -> dict:
    """稿子各项指标 vs 拆解时参考笔记的均值。没有参考统计时不评量化分。"""
    f = stats.features({"title": draft.get("title"), "content": draft.get("content"), "tags": draft.get("tags"), "images": []})
    if not ref:
        return {"items": [], "score": None}
    items = []
    for k, label, mode, tol in BENCH:
        if k not in ref:
            continue
        v, t = float(f[k]), float(ref[k])
        if mode == "rel":
            lo, hi = t * (1 - tol), t * (1 + tol)
            ok, target = lo <= v <= hi, f"{_fmt(k, lo)}–{_fmt(k, hi)}"
        elif mode == "abs":
            lo, hi = max(0.0, t - tol), t + tol
            ok, target = lo <= v <= hi, f"{_fmt(k, lo)}–{_fmt(k, hi)}"
        else:
            need = t >= 0.5
            ok, target = (bool(v) == need) if need else True, "要有" if need else "不限"
        items.append({"key": k, "label": label, "value": _fmt(k, v), "target": target, "ok": ok})
    return {"items": items, "score": round(100 * sum(i["ok"] for i in items) / len(items)) if items else None}


def quant_text(q: dict) -> str:
    if not q["items"]:
        return "(没有参考统计,不做量化对比)"
    head = f"(对照基准:{q['bench']})\n" if q.get("bench") else ""
    return head + "\n".join(f"- {i['label']}:稿子 {i['value']},参考范围 {i['target']},{'达标' if i['ok'] else '不达标'}" for i in q["items"])


def checks_text(c: dict) -> str:
    lines = [f"- [{h['category']}] 「{h['word']}」在{llm.FIELD_NAMES.get(h['field'], h['field'])}" for h in c["hits"]]
    lines += [f"- [占位符,保留] 【补充:{p['text']}】" for p in c["placeholders"]]
    return "\n".join(lines) or "(无问题)"


def grade_of(score: float) -> str:
    return next(g for line, g in GRADES if score >= line)


def run(project: dict, playbook: dict, pb_stats: dict, draft: dict, kb: str, with_comments: bool) -> dict:
    pbs = pb_stats or {}
    if pbs.get("comparable") and pbs.get("top"):  # 样本够就以爆款/热门组为基准,不被所选的几篇参考带偏
        bench, label = pbs["top"], ("同主题" if str(pbs.get("scope", "")).startswith("topic:") else "同赛道") + f"爆款/热门 {pbs['top']['n']} 篇"
    else:
        bench = pbs.get("ref")
        label = f"所选参考 {bench['n']} 篇" if bench else ""
    q = quant(draft, bench)
    q["bench"] = label
    c = compliance.scan(draft["title"], draft["content"], draft["tags"], draft.get("image_prompts"))
    out = llm.review(project, playbook, draft, quant_text(q), checks_text(c), kb, compliance.banned_for_prompt(), with_comments)
    raw = out.get("scores") or {}
    dims = {d: max(0, min(100, int(float(raw.get(d, 0) or 0)))) for d in llm.REVIEW_DIMS}
    base = mean(dims.values()) if q["score"] is None else mean(dims.values()) * 0.75 + q["score"] * 0.25
    penalty = 8 * c["high"]
    overall = max(0, round(base - penalty))
    rv = out.get("revised") or {}
    revised = None
    if rv.get("content"):
        revised = {"title": str(rv.get("title") or draft["title"]), "content": str(rv["content"]),
                   "tags": [str(t).lstrip("#") for t in (rv.get("tags") or draft["tags"])]}
        revised["checks"] = compliance.scan(revised["title"], revised["content"], revised["tags"], draft.get("image_prompts"))
        # 修改版弄丢了作者要补的占位符时提醒,不自动采用
        lost = {p["text"] for p in c["placeholders"]} - {p["text"] for p in revised["checks"]["placeholders"]}
        revised["lost_placeholders"] = sorted(lost)
    return {
        "overall": overall, "grade": grade_of(overall), "dims": dims, "quant": q, "penalty": penalty,
        "summary": str(out.get("summary") or ""),
        "issues": [i for i in (out.get("issues") or []) if isinstance(i, dict)][:8],
        "revised": revised,
        "comments": [x for x in (out.get("comments") or []) if isinstance(x, dict)][:8] if with_comments else [],
        "applied": False,
    }
