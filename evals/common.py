"""测试共用:报告类、小工具、路径常量。"""
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FIXTURES, RESULTS, BASELINE = ROOT / "fixtures", ROOT / "results", ROOT / "baseline.json"
DROP = 0.2  # 某项通过率比基线下降超过这么多算退化


# ---------- 小工具 ----------
def _numbers(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text)]


def _shared_run(a: str, b: str, n: int) -> str | None:
    """a、b 是否有长度 ≥ n 的相同片段(去掉空白和标点后比较),用于抓照抄。"""
    clean = lambda s: re.sub(r"[\s\W_]+", "", s)
    a, b = clean(a), clean(b)
    grams = {b[i:i + n] for i in range(len(b) - n + 1)}
    return next((a[i:i + n] for i in range(len(a) - n + 1) if a[i:i + n] in grams), None)


def _jaccard(a: str, b: str) -> float:
    bg = lambda s: {s[i:i + 2] for i in range(len(s) - 1)}
    x, y = bg(a), bg(b)
    return len(x & y) / max(len(x | y), 1)


class Report:
    def __init__(self):
        self.rows = []  # (检查项, 是否通过, 说明)

    def check(self, name: str, ok: bool, detail: str = ""):
        self.rows.append((name, bool(ok), detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""))

    def rates(self) -> dict:
        agg = {}
        for name, ok, _ in self.rows:
            agg.setdefault(name, []).append(ok)
        return {k: round(sum(v) / len(v), 3) for k, v in agg.items()}


def _raises2(fn):
    try:
        fn()
        return False
    except Exception:
        return True
