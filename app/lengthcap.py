"""正文字数的软约束:基准可信、并且明显超标时才压缩,压缩版和原稿都保留,用户可以来回切换。

触发条件(都满足才压):
1. 同主题(或同赛道)爆款组和普通组样本都够多(stats["comparable"]);
2. 所选参考和爆款组的字数/段落数差距不大(没有 stats["warnings"]),说明基准可信;
3. 正文字数超过基准的 CAP_RATIO 倍。
教程 / 攻略 / 清单 / 测评类靠信息量取胜,基准放宽到「爆款组和普通组里较大的那个」。"""
import re

CAP_RATIO = 2.5      # 超过基准的 2.5 倍才压
TARGET_RATIO = 1.5   # 压到基准的 1.5 倍左右
TARGET_MIN = 60
INFO_TYPE = re.compile(r"教程|做法|攻略|步骤|清单|指南|怎么|如何|合集|挑选|选购|测评|评测|对比|避坑|科普")


def plan(stats: dict | None, angle: str, content: str) -> dict | None:
    """需要压缩就返回 {baseline, cap, target, basis};不需要返回 None。"""
    st = stats or {}
    top, normal = st.get("top"), st.get("normal")
    if not (st.get("comparable") and top and top.get("body_len")) or st.get("warnings"):
        return None
    info = bool(INFO_TYPE.search(angle or ""))
    baseline = max(top["body_len"], (normal or {}).get("body_len") or 0) if info else top["body_len"]
    cap = baseline * CAP_RATIO
    if len(content or "") <= cap:
        return None
    scope = st.get("scope") or ""
    label = (scope[6:] if scope.startswith("topic:") else st.get("track") or "赛道") + ("爆款/普通组较大值" if info else "爆款组")
    return {"baseline": round(baseline), "cap": round(cap), "target": max(TARGET_MIN, round(baseline * TARGET_RATIO)), "basis": label}


def accept(old: str, new: str, holes: list[str], new_holes: list[str]) -> bool:
    """压缩版能不能用:明显更短,并且原稿里的占位符一个没丢。"""
    return bool(new and new.strip()) and len(new) < len(old) * 0.9 and set(holes) <= set(new_holes)


def swap(content: str, variant: dict) -> tuple[str, dict]:
    """在压缩版和原稿之间切换:返回 (新正文, 新 variant)。"""
    if not variant or "other" not in variant:
        raise ValueError("没有可切换的版本")
    v = dict(variant)
    new, v["other"] = v["other"], content
    v["showing"] = "long" if v.get("showing") == "short" else "short"
    return new, v
