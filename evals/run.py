"""回归测试:改提示词、改规则之后跑一遍,和基线对比,防止效果变差。

  离线(不调 API,几秒钟):  python -m evals.run --offline
  在线(调 DeepSeek):        python -m evals.run --live [--fixture beauty_synthetic] [--repeat 2]
  把本次在线结果设为基线:   python -m evals.run --live --save-baseline
  从笔记库生成真实样本集:   python -m evals.run --snapshot 美妆护肤 --name beauty_real [--keyword 油皮底妆]

Docker 部署时在容器里跑:   docker compose exec workbench python -m evals.run --offline
在线测试只读数据库(取 Key、合规词库、知识库),不写任何业务数据。"""
import argparse
import json
import os
import re
import sys
import time
from itertools import combinations
from pathlib import Path

from .cases import offline_basic, offline_image, offline_llm, offline_schema
from .common import BASELINE, DROP, FIXTURES, RESULTS, ROOT, Report, _jaccard, _numbers, _raises2, _shared_run
from .isolated import isolated


def offline(rep: Report):
    for m in (offline_basic, offline_image, offline_llm, offline_schema):
        m.run(rep)


# ---------- 在线 ----------
def live_once(fx: dict, rep: Report, timings: dict):
    from app import compliance, llm, review, stats, worker

    project, refs = fx["project"], fx["ref"]
    kb = worker.knowledge(project["track"])
    st = stats.from_groups(project["track"], refs, fx.get("top", []), fx.get("normal", []))

    print("\n[拆解]")
    t0 = time.time()
    card = worker._analyze_checked(project, refs, st)  # 和真实流程一致:含「选题冲突」检查与重做
    timings.setdefault("拆解", []).append(time.time() - t0)
    need = ["name", "summary", "benchmarks", "title_formulas", "hooks", "structure", "tone", "format", "dos", "donts", "evidence"]
    missing = [k for k in need if not card.get(k)]
    rep.check("拆解:字段齐全", not missing, f"缺 {missing}")
    values = [v for g in (st["ref"], st["top"], st["normal"]) if g for k, v in g.items() if k != "n"]
    cands = {round(x, 1) for v in values for x in (v, v * 100)}
    bms = [str(b) for b in card.get("benchmarks") or []]
    grounded = [b for b in bms if any(abs(n - c) <= max(0.15, c * 0.05) for n in _numbers(b) for c in cands)]
    rep.check("拆解:量化规律引用了统计数字(≥80%)", bms and len(grounded) / len(bms) >= 0.8, f"{len(grounded)}/{len(bms)}")
    ev = card.get("evidence") or {}
    rep.check("拆解:标注了依据", isinstance(ev, dict) and len(ev) >= 6, str(ev)[:120])
    topics = card.get("topics") or []
    rep.check("拆解:列出了笔记里实际涉及的主题", isinstance(topics, list) and 1 <= len(topics) <= 8, str(topics))
    kws = project.get("keywords") or []
    rep.check("拆解:卡片名称里没有把不相关的关键词糊在一起", not any(a in str(card.get("name")) and b in str(card.get("name"))
              and not any(a in (r["title"] + r["content"]) and b in (r["title"] + r["content"]) for r in refs) for a, b in combinations(kws, 2)), str(card.get("name")))

    print("\n[规划角度]")
    angles = llm.plan_angles(project, card, 3)
    rep.check("角度:数量为 3 且不为空", len([a for a in angles if a.strip()]) == 3)
    sim = max((_jaccard(a, b) for a, b in combinations(angles, 2)), default=0)
    rep.check("角度:彼此不雷同(相似度<0.5)", sim < 0.5, f"最高相似度 {sim:.2f}")

    print("\n[复刻]")
    banned = compliance.banned_for_prompt()
    drafts = []
    for i, angle in enumerate(angles):
        t0 = time.time()
        out = llm.write_note(project, card, refs, angle, worker.image_block(3), kb, banned)
        for k, d in (("title", ""), ("content", ""), ("alt_titles", []), ("tags", [])):
            out.setdefault(k, d)
        from app import covers as _cv
        out["image_prompts"], out["cover_card"] = _cv.plan(i, out, project["track"], project["track"], 3, worker._safe_text)
        out, checks = worker._check_and_fix(out, auto=True)
        timings.setdefault("复刻(含自动修正)", []).append(time.time() - t0)
        out["angle"] = angle
        drafts.append(out)
        tag = f"#{i + 1}"
        rep.check("复刻:标题 ≤ 20 字", len(out["title"]) <= 20, f"{tag} {out['title']}({len(out['title'])}字)")
        rep.check("复刻:无高风险词(自动修正后)", checks["high"] == 0, f"{tag} {[h['word'] for h in checks['hits'] if h['severity'] == 'high']}")
        copied = next((s for r in refs if (s := _shared_run(out["content"], r["content"] or "", 14))), None)
        rep.check("复刻:没有照抄参考原文(14 字以上)", copied is None, f"{tag} 「{copied}」")
        ref_len = (st["ref"] or {}).get("body_len") or 0
        rep.check("复刻:正文长度合理(参考的 0.5~3 倍)", not ref_len or 0.5 * ref_len <= len(out["content"]) <= 3 * ref_len,
                  f"{tag} {len(out['content'])} 字,参考 {ref_len:.0f}")
        rep.check("复刻:话题 5-8 个", 5 <= len(out["tags"]) <= 8, f"{tag} {len(out['tags'])} 个")
        text_all = out["title"] + out["content"]
        ref_texts = [(r["title"] or "") + (r["content"] or "") for r in refs]
        forced = [(a, b) for a, b in combinations(kws, 2) if a in text_all and b in text_all and not any(a in t and b in t for t in ref_texts)]
        rep.check("复刻:没有把不相关的关键词强行组合", not forced, f"{tag} {forced}")
        rep.check("复刻:封面带主标题", bool(out["cover_card"].get("title")) and bool(compliance.image_texts((out.get("image_prompts") or [])[:1])), f"{tag} {(out.get('image_prompts') or [''])[0][:60]}")
        rep.check("复刻:图片说明条数正好是 3(封面 + 2 张内页),内页无字", len(out.get("image_prompts") or []) == 3 and not compliance.image_texts(out["image_prompts"][1:]), f"{tag} {len(out.get('image_prompts') or [])} 条")

    print("\n[审稿]")
    t0 = time.time()
    r = review.run(project, card, st, drafts[0], kb, with_comments=True)
    timings.setdefault("审稿", []).append(time.time() - t0)
    rep.check("审稿:等级与各维度评分", r["grade"] in "SABCD" and len(r["dims"]) == len(llm.REVIEW_DIMS) and all(0 < v <= 100 for v in r["dims"].values()), str(r["dims"]))
    issues = r["issues"]
    rep.check("审稿:问题≥2条且都有可执行修改", len(issues) >= 2 and all(i.get("fix") for i in issues), f"{len(issues)} 条")
    rep.check("审稿:不预测效果数字", not any(re.search(r"(点击率|互动率|涨粉|曝光).{0,6}[+＋]?\d+%", str(i)) for i in issues))
    rv = r["revised"] or {}
    rep.check("审稿:给出修改版", bool(rv.get("content")))
    if rv:
        rep.check("审稿:修改版无高风险词", rv["checks"]["high"] == 0, str([h["word"] for h in rv["checks"]["hits"]]))
        rep.check("审稿:修改版保留占位符", not rv["lost_placeholders"], str(rv["lost_placeholders"]))
    rep.check("审稿:模拟评论含质疑和提问", any(c.get("type") == "质疑" for c in r["comments"]) and any(c.get("type") == "提问" for c in r["comments"]))
    return {"card": card, "drafts": drafts, "review": r}


def compare(rates: dict, base: dict) -> list[str]:
    bad = []
    for k, v in rates.items():
        b = base.get(k)
        if b is not None and v < b - DROP + 1e-9:
            bad.append(f"{k}:{b:.0%} → {v:.0%}")
    return bad


def snapshot(track: str, name: str, keyword: str, n: int, keywords: list[str] | None = None):
    """从笔记库取真实笔记做成固定样本集,之后每次测试都用同一批数据,结果才可比。"""
    from app.db import q_all, tier_sql
    t = tier_sql("n")
    cond, args = "n.track=?", [track]
    if keyword:
        cond += " AND n.keyword=?"; args.append(keyword)
    cols = "n.id, n.uid AS note_id, n.title, n.content, n.likes, n.collects, n.comments, n.tags, n.images"
    top = q_all(f"SELECT {cols} FROM notes n WHERE {cond} AND {t} IN ('viral','hot') AND n.content!='' ORDER BY n.likes DESC LIMIT ?", *args, n)
    normal = q_all(f"SELECT {cols} FROM notes n WHERE {cond} AND {t}='normal' AND n.content!='' ORDER BY n.likes DESC LIMIT ?", *args, n)
    if len(top) < 3:
        sys.exit(f"{track} 下有正文的爆款/热门笔记只有 {len(top)} 篇,至少要 3 篇")
    fx = {"name": name, "synthetic": False, "note": f"从笔记库导出:赛道 {track} {keyword},{time.strftime('%Y-%m-%d')}",
          "project": {"track": track, "keywords": keywords or [keyword or track], "direction": ""},
          "ref": top[:5], "top": top, "normal": normal}
    path = FIXTURES / f"{name}.json"
    path.write_text(json.dumps(fx, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已生成 {path}:参考 {len(fx['ref'])} 篇,爆款组 {len(top)} 篇,普通组 {len(normal)} 篇")



def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--fixture", action="append", help="样本集名称,可多次指定;默认全部")
    ap.add_argument("--repeat", type=int, default=1, help="每个样本集重复跑几次,降低随机性")
    ap.add_argument("--save-baseline", action="store_true")
    ap.add_argument("--snapshot", metavar="赛道")
    ap.add_argument("--name", default="")
    ap.add_argument("--keyword", default="")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--keywords", default="", help="--snapshot 时,项目的关键词列表(逗号分隔),用来检查复刻有没有把不相关的词强行组合")
    a = ap.parse_args()
    if not Path("/.dockerenv").exists() and not os.environ.get("EVALS_ALLOW_HOST"):
        sys.exit("请在容器里运行:docker compose exec workbench python -m evals.run ...\n"
                 "在宿主机上直接打开正在被容器使用的 SQLite 数据库,会破坏它的 WAL 锁,导致容器读不了数据库。")
    if a.snapshot:
        return snapshot(a.snapshot, a.name or f"{a.snapshot}_real", a.keyword, a.n, [k.strip() for k in re.split(r"[,，]", a.keywords) if k.strip()])
    if not (a.offline or a.live):
        ap.print_help(); return

    rep, timings, outputs = Report(), {}, {}
    offline_failed = False
    if a.offline:
        print("====== 离线测试 ======")
        offline(rep)
        isolated(rep)
    offline_failed = any(not ok for _, ok, _ in rep.rows)
    if a.live:
        names = a.fixture or sorted(p.stem for p in FIXTURES.glob("*.json"))
        for name in names:
            fx = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
            for i in range(a.repeat):
                print(f"\n====== 在线测试:{name}{'(合成样本)' if fx.get('synthetic') else ''} 第 {i + 1}/{a.repeat} 轮 ======")
                try:
                    outputs[f"{name}#{i + 1}"] = live_once(fx, rep, timings)
                except Exception as e:
                    rep.check("在线流程跑通", False, repr(e))

    rates = rep.rates()
    total = sum(ok for _, ok, _ in rep.rows)
    print(f"\n====== 结果:{total}/{len(rep.rows)} 项通过 ======")
    for k, v in timings.items():
        print(f"  耗时 {k}:平均 {sum(v) / len(v):.1f}s")
    RESULTS.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    result = {"time": stamp, "mode": "live" if a.live else "offline", "rates": rates,
              "timings": {k: round(sum(v) / len(v), 1) for k, v in timings.items()}, "outputs": outputs,
              "failures": [{"check": n, "detail": d} for n, ok, d in rep.rows if not ok]}
    (RESULTS / f"{stamp}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  详细结果(含生成内容):evals/results/{stamp}.json")

    regressions = []
    if a.live and BASELINE.exists() and not a.save_baseline:
        base = json.loads(BASELINE.read_text(encoding="utf-8"))
        regressions = compare(rates, base["rates"])
        print(f"\n对比基线({base['time']}):" + ("无退化" if not regressions else ""))
        for r in regressions:
            print(f"  退化  {r}")
    if a.save_baseline:
        if not a.live:
            sys.exit("基线只记录在线测试结果,请加 --live")
        BASELINE.write_text(json.dumps({"time": stamp, "rates": rates}, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n已保存为基线:evals/baseline.json")
    sys.exit(1 if regressions or offline_failed else 0)  # 离线测试是确定性的,任何一项失败都算失败


if __name__ == "__main__":
    main()
