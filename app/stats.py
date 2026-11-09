"""样本统计:把「爆款长什么样」变成可核对的数字,作为拆解的事实依据。
对比三组:本次选中的参考笔记、同赛道爆款/热门、同赛道普通笔记。"""
import re
from collections import Counter
from statistics import mean, median
from .db import q_all, tier_sql

MIN_GROUP = 5  # 一组少于这么多篇就不出对比结论,只作参考
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐✅❌]")
LIST_LINE = re.compile(r"^\s*(\d+[.、)）]|[①-⑩]|[1-9]️?⃣|[•·\-—▪️✅✔️👉]|第[一二三四五六七八九十\d]+[步点个])")
CTA = re.compile(r"评论|留言|你们|姐妹|宝子|大家|收藏|关注|[吗呢吧][？?]|[？?]\s*$")

FEATURES = [  # (键, 名称, 格式)
    ("title_len", "标题字数", "{:.1f}"),
    ("t_digit", "标题含数字", "{:.0%}"),
    ("t_mark", "标题含！/？", "{:.0%}"),
    ("t_emoji", "标题含 emoji", "{:.0%}"),
    ("body_len", "正文字数(中位数)", "{:.0f}"),
    ("paras", "段落数", "{:.1f}"),
    ("emoji_100", "每百字 emoji", "{:.1f}"),
    ("list_ratio", "分点/编号段落占比", "{:.0%}"),
    ("cta", "结尾有互动引导", "{:.0%}"),
    ("tags", "话题数", "{:.1f}"),
    ("images", "配图数", "{:.1f}"),
]


def _one(n: dict) -> dict:
    t, body = n.get("title") or "", n.get("content") or ""
    lines = [l for l in body.splitlines() if l.strip()]
    return {
        "title_len": len(t), "t_digit": bool(re.search(r"\d|[一二三四五六七八九十两]+[个步款天招种分]", t)),
        "t_mark": bool(re.search(r"[!！?？]", t)), "t_emoji": bool(EMOJI.search(t)),
        "body_len": len(body), "paras": len(lines),
        "emoji_100": len(EMOJI.findall(body)) / max(len(body), 1) * 100,
        "list_ratio": sum(1 for l in lines if LIST_LINE.match(l)) / max(len(lines), 1),
        "cta": bool(CTA.search(body[-80:])) if body else False,
        "tags": len(n.get("tags") or []), "images": len(n.get("images") or []),
    }


def _agg(notes: list[dict]) -> dict | None:
    if not notes:
        return None
    fs = [_one(n) for n in notes]
    out = {"n": len(notes)}
    for k, _, _ in FEATURES:
        vals = [float(f[k]) for f in fs]
        out[k] = median(vals) if k == "body_len" else mean(vals)
    return out


def features(n: dict) -> dict:
    return _one(n)


def _warnings(ref, top, comparable) -> list[str]:
    """所选参考和爆款组差得很远 = 所选参考不典型,提醒模型和作者别被带偏。"""
    out = []
    if ref and top and comparable:
        for k, label in (("body_len", "正文字数中位数"), ("paras", "段落数")):
            r, t = ref[k], top[k]
            if t and (r < t * 0.6 or r > t * 1.6):
                out.append(f"所选参考的{label}是 {r:.0f},爆款组是 {t:.0f},差别很大,说明所选参考不典型;字数和排版以爆款组为准,不要据此下「短(长)正文也能爆」的结论。")
    return out


def from_groups(track: str, ref: list[dict], top: list[dict], normal: list[dict], scope: str = "") -> dict:
    """纯函数版:直接给三组笔记。scope 是爆款组/普通组的范围:topic:xxx(同主题)或 track:xxx(同赛道)。"""
    st = {"track": track, "scope": scope or f"track:{track}", "ref": _agg(ref), "top": _agg(top), "normal": _agg(normal),
          "comparable": len(top) >= MIN_GROUP and len(normal) >= MIN_GROUP}
    st["warnings"] = _warnings(st["ref"], st["top"], st["comparable"])
    return st


def _label(st: dict) -> str:
    s = st.get("scope") or ""
    return s[6:] if s.startswith("topic:") else st["track"]


def build(track: str, ref_notes: list[dict]) -> dict:
    """爆款组/普通组优先取「和所选参考同主题」的笔记,同主题样本不够再退回整个赛道。视频笔记不参与统计。"""
    tier = tier_sql("n")
    refs = [n for n in ref_notes if n.get("note_type") != "video"] or ref_notes
    if not track:
        return from_groups(track, refs, [], [])

    def groups(extra, args):
        base = f"SELECT n.* FROM notes n WHERE n.track=? AND n.note_type!='video' AND n.dup_of IS NULL {extra} AND "
        return q_all(base + f"{tier} IN ('viral','hot')", track, *args), q_all(base + f"{tier}='normal'", track, *args)
    topics = Counter(n.get("topic") for n in refs if n.get("topic") and n.get("topic") != "未识别")
    if topics:
        topic = topics.most_common(1)[0][0]
        top, normal = groups("AND n.topic=?", [topic])
        if len(top) >= MIN_GROUP and len(normal) >= MIN_GROUP:
            return from_groups(track, refs, top, normal, f"topic:{topic}")
    top, normal = groups("", [])
    return from_groups(track, refs, top, normal)


def to_prompt(st: dict) -> str:
    """转成给模型看的表格。样本不足时明确告诉模型不要据此下对比结论。"""
    label = _label(st)
    cols = [("本次参考", st["ref"]), (f"{label}爆款/热门", st["top"]), (f"{label}普通", st["normal"])]
    cols = [(name, g) for name, g in cols if g]
    if not cols:
        return "(没有可用的样本统计)"
    lines = ["| 指标 | " + " | ".join(f"{name}(n={g['n']})" for name, g in cols) + " |",
             "|---|" + "---|" * len(cols)]
    for k, label, f in FEATURES:
        lines.append(f"| {label} | " + " | ".join(f.format(g[k]) for _, g in cols) + " |")
    note = ("爆款组与普通组样本都足够,可以据此总结差异。" if st["comparable"]
            else f"{label}的爆款组或普通组不足 {MIN_GROUP} 篇,不要据此下「爆款比普通更…」的对比结论,只描述参考笔记本身的特征。")
    return "\n".join(lines) + "\n" + note + "".join(f"\n注意:{w}" for w in st.get("warnings") or [])
