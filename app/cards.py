"""封面卡片:字由程序排版(HTML/CSS),再用无界面 Chromium 截图成 PNG;AI 只负责出无字的实拍图。
这样字体、字号、位置完全可控,不会写错字;版式和配色可以按内容挑选、在同一批里轮换。

版式(LAYOUTS):有的需要一张实拍图(photo=True),有的是纯文字封面。
配色(THEMES):6 套,每套只有一个强调色。
数据(data):title 主标题、sub 副标题、points 要点(清单类用)、num 数字(「3 种吃法」的 3)、tag 小标签、brand 产品名。"""
import html
import shutil
import subprocess
import tempfile
from pathlib import Path

W, H = 1080, 1440  # 小红书 3:4
SANS = "'Noto Sans CJK SC','Noto Sans SC','Source Han Sans SC','PingFang SC','Microsoft YaHei',sans-serif"
SERIF = "'Noto Serif CJK SC','Noto Serif SC','Source Han Serif SC','Songti SC',serif"

THEMES = {
    "cream":  {"bg": "#F6EFE3", "paper": "#FFFBF3", "ink": "#2B2118", "sub": "#7A6A58", "accent": "#D9472B", "on": "#FFFFFF", "line": "#E2D6C3"},
    "ink":    {"bg": "#141414", "paper": "#1E1E1E", "ink": "#F5F1E8", "sub": "#A39E93", "accent": "#F2C14E", "on": "#141414", "line": "#3A3A3A"},
    "klein":  {"bg": "#F3F4F7", "paper": "#FFFFFF", "ink": "#10131A", "sub": "#5A6070", "accent": "#1F3FBF", "on": "#FFFFFF", "line": "#D9DCE4"},
    "lemon":  {"bg": "#FFF3B0", "paper": "#FFFBE6", "ink": "#1D1B12", "sub": "#6B6447", "accent": "#1D1B12", "on": "#FFF3B0", "line": "#E9D97A"},
    "forest": {"bg": "#E7EEE4", "paper": "#F7FAF4", "ink": "#1E2B22", "sub": "#5D6E61", "accent": "#2F6B45", "on": "#FFFFFF", "line": "#C9D6C5"},
    "tomato": {"bg": "#FFE7DF", "paper": "#FFF7F3", "ink": "#2A1410", "sub": "#7D5A50", "accent": "#E2402B", "on": "#FFFFFF", "line": "#F2C8BC"},
}


def _e(s) -> str:
    return html.escape(str(s or ""))


def _fs(text: str, width: int, lines: float, lo: int, hi: int) -> int:
    """按字数估字号:中文一个字约一个字号宽,希望在 width 宽度里排成 lines 行。"""
    n = max(1, len(text or ""))
    return max(lo, min(hi, int(width * lines / (n + 0.6))))


def _pts(d, n=4):
    return [p for p in (d.get("points") or []) if p][:n]


# ---------- 版式 ----------
def l_photo_top(d, t, img):
    fs = _fs(d["title"], 940, 2, 72, 150)
    return f"""<img class="full" src="{img}">
<div style="position:absolute;left:0;right:0;top:0;padding:80px 70px 60px;background:rgba(0,0,0,.28)">
  {f'<span class="pill" style="background:{t["accent"]};color:{t["on"]}">{_e(d["tag"])}</span>' if d.get("tag") else ""}
  <h1 style="font:900 {fs}px/1.12 {SANS};color:#fff;margin:22px 0 0;letter-spacing:.02em">{_e(d["title"])}</h1>
  {f'<p style="font:500 40px/1.4 {SANS};color:rgba(255,255,255,.9);margin:18px 0 0">{_e(d["sub"])}</p>' if d.get("sub") else ""}
</div>"""


def l_split_top(d, t, img):
    fs = _fs(d["title"], 940, 2, 70, 140)
    return f"""<div style="position:absolute;inset:0 0 auto 0;height:560px;background:{t['accent']};padding:90px 70px 0">
  <h1 style="font:900 {fs}px/1.12 {SANS};color:{t['on']};margin:0">{_e(d["title"])}</h1>
  {f'<p style="font:500 40px/1.4 {SANS};color:{t["on"]};opacity:.85;margin:22px 0 0">{_e(d["sub"])}</p>' if d.get("sub") else ""}
</div><img style="position:absolute;left:0;right:0;bottom:0;width:100%;height:880px;object-fit:cover" src="{img}">"""


def l_split_bottom(d, t, img):
    fs = _fs(d["title"], 940, 2, 66, 120)
    return f"""<img style="position:absolute;left:0;right:0;top:0;width:100%;height:900px;object-fit:cover" src="{img}">
<div style="position:absolute;left:0;right:0;bottom:0;height:540px;background:{t['paper']};padding:60px 70px">
  <div style="width:90px;height:10px;background:{t['accent']};margin-bottom:34px"></div>
  <h1 style="font:900 {fs}px/1.15 {SANS};color:{t['ink']};margin:0">{_e(d["title"])}</h1>
  {f'<p style="font:400 38px/1.5 {SANS};color:{t["sub"]};margin:20px 0 0">{_e(d["sub"])}</p>' if d.get("sub") else ""}
</div>"""


def l_statement(d, t, img):
    fs = _fs(d["title"], 920, 3.2, 96, 210)
    return f"""<div style="position:absolute;inset:0;padding:120px 80px;display:flex;flex-direction:column;justify-content:center">
  {f'<p style="font:700 40px/1 {SANS};color:{t["accent"]};margin:0 0 50px;letter-spacing:.1em">{_e(d["tag"])}</p>' if d.get("tag") else ""}
  <h1 style="font:900 {fs}px/1.1 {SANS};color:{t['ink']};margin:0">{_e(d["title"])}</h1>
  {f'<p style="font:500 44px/1.45 {SANS};color:{t["sub"]};margin:56px 0 0;padding-left:28px;border-left:10px solid {t["accent"]}">{_e(d["sub"])}</p>' if d.get("sub") else ""}
  {f'<p style="position:absolute;left:80px;bottom:90px;font:500 32px/1 {SANS};color:{t["sub"]};margin:0">{_e(d["brand"])}</p>' if d.get("brand") else ""}
</div>"""


def l_memo(d, t, img):
    pts = _pts(d, 5)
    fs = _fs(d["title"], 940, 2, 60, 96)
    return f"""<div style="position:absolute;inset:0;background:#FDFCF8;padding:70px 64px">
  <div style="display:flex;justify-content:space-between;font:500 34px/1 {SANS};color:#D9A21B"><span>‹ 备忘录</span><span>···</span></div>
  <p style="font:400 28px/1 {SANS};color:#A9A398;text-align:center;margin:46px 0 30px">今天 08:30</p>
  <h1 style="font:800 {fs}px/1.3 {SANS};color:#1C1B19;margin:0 0 40px">{_e(d["title"])}</h1>
  {"".join(f'<p style="font:400 46px/1.6 {SANS};color:#2E2C28;margin:0 0 18px;padding-bottom:18px;border-bottom:2px solid #EFEBE2">{i + 1}. {_e(p)}</p>' for i, p in enumerate(pts))}
  {f'<p style="font:400 40px/1.6 {SANS};color:#6E6A62;margin:30px 0 0">{_e(d["sub"])}</p>' if d.get("sub") else ""}
</div>"""


def l_checklist(d, t, img):
    pts = _pts(d, 4)
    fs = _fs(d["title"], 900, 2, 66, 120)
    photo = f'<img style="position:absolute;right:70px;top:80px;width:260px;height:260px;border-radius:50%;object-fit:cover;border:10px solid {t["paper"]}" src="{img}">' if img else ""
    pad = {4: 34, 3: 52}.get(len(pts), 70)  # 要点少时把每一行撑高,整张卡不会下半部分空着
    fsz = {4: 50, 3: 56}.get(len(pts), 62)
    return f"""<div style="position:absolute;inset:0;padding:90px 70px;display:flex;flex-direction:column;justify-content:center">
  {photo}
  <p style="font:700 36px/1 {SANS};color:{t['accent']};margin:0 0 30px;letter-spacing:.08em">{_e(d.get("tag") or "CHECKLIST")}</p>
  <h1 style="font:900 {fs}px/1.15 {SANS};color:{t['ink']};margin:0 0 70px;max-width:{"640px" if img else "940px"}">{_e(d["title"])}</h1>
  {"".join(f'<div style="display:flex;align-items:center;gap:30px;background:{t["paper"]};border-radius:24px;padding:{pad}px 40px;margin-bottom:24px"><span style="flex:none;width:64px;height:64px;border-radius:16px;background:{t["accent"]};color:{t["on"]};font:900 44px/64px {SANS};text-align:center">✓</span><span style="font:700 {fsz}px/1.3 {SANS};color:{t["ink"]}">{_e(p)}</span></div>' for p in pts)}
</div>"""


def l_editorial(d, t, img):
    fs = _fs(d["title"], 900, 2, 64, 112)
    return f"""<div style="position:absolute;inset:0;background:{t['paper']};padding:70px 70px">
  <div style="display:flex;justify-content:space-between;align-items:baseline;border-bottom:3px solid {t['ink']};padding-bottom:18px">
    <span style="font:700 40px/1 {SERIF};color:{t['ink']};letter-spacing:.06em">{_e(d.get("brand") or "生活志")}</span><span style="font:400 28px/1 {SERIF};color:{t['sub']}">VOL.{_e(d.get("vol") or "07")}</span></div>
  <img style="display:block;width:100%;height:760px;object-fit:cover;margin:40px 0 44px" src="{img}">
  <h1 style="font:900 {fs}px/1.22 {SERIF};color:{t['ink']};margin:0">{_e(d["title"])}</h1>
  {f'<p style="font:400 36px/1.5 {SERIF};color:{t["sub"]};margin:20px 0 0">{_e(d["sub"])}</p>' if d.get("sub") else ""}
</div>"""


def l_sticker(d, t, img):
    fs = _fs(d["title"], 940, 2, 66, 118)
    return f"""<img class="full" src="{img}">
<div style="position:absolute;right:60px;top:90px;transform:rotate(8deg);background:{t['accent']};color:{t['on']};font:900 52px/1 {SANS};padding:26px 36px;border-radius:999px;border:6px solid #fff">{_e(d.get("tag") or "亲测")}</div>
<div style="position:absolute;left:0;right:0;bottom:0;background:{t['paper']};padding:50px 70px 64px">
  <h1 style="font:900 {fs}px/1.15 {SANS};color:{t['ink']};margin:0">{_e(d["title"])}</h1>
  {f'<p style="font:500 38px/1.4 {SANS};color:{t["sub"]};margin:16px 0 0">{_e(d["sub"])}</p>' if d.get("sub") else ""}
</div>"""


def l_big_number(d, t, img):
    num, rest = str(d.get("num") or "3"), d.get("unit") or "个要点"
    fs = _fs(d["title"], 920, 2, 60, 100)
    return f"""<div style="position:absolute;inset:0;padding:100px 80px">
  <div style="font:900 520px/0.9 {SANS};color:{t['accent']};letter-spacing:-.04em">{_e(num)}</div>
  <div style="font:900 110px/1.1 {SANS};color:{t['ink']};margin:10px 0 60px">{_e(rest)}</div>
  <h1 style="font:700 {fs}px/1.3 {SANS};color:{t['ink']};margin:0;padding-top:40px;border-top:4px solid {t['ink']}">{_e(d["title"])}</h1>
  {f'<p style="font:400 40px/1.5 {SANS};color:{t["sub"]};margin:24px 0 0">{_e(d["sub"])}</p>' if d.get("sub") else ""}
</div>"""


def l_question(d, t, img):
    fs = _fs(d["title"], 900, 3, 90, 170)
    return f"""<div style="position:absolute;right:-60px;top:-120px;font:900 1200px/1 {SERIF};color:{t['accent']};opacity:.16">?</div>
<div style="position:absolute;inset:0;padding:140px 80px;display:flex;flex-direction:column;justify-content:flex-end">
  <h1 style="font:900 {fs}px/1.12 {SANS};color:{t['ink']};margin:0">{_e(d["title"])}</h1>
  {f'<p style="display:inline-block;align-self:flex-start;font:700 44px/1 {SANS};color:{t["on"]};background:{t["accent"]};padding:22px 30px;margin:60px 0 0">{_e(d["sub"])}</p>' if d.get("sub") else ""}
</div>"""


def l_polaroid(d, t, img):
    fs = _fs(d["title"], 920, 2, 64, 112)
    return f"""<div style="position:absolute;inset:0;background:{t['bg']};background-image:radial-gradient({t['line']} 3px,transparent 3px);background-size:44px 44px"></div>
<div style="position:absolute;left:70px;right:70px;top:90px">
  <h1 style="font:900 {fs}px/1.15 {SANS};color:{t['ink']};margin:0">{_e(d["title"])}</h1></div>
<div style="position:absolute;left:150px;top:470px;width:780px;background:#fff;padding:36px 36px 120px;transform:rotate(-4deg);box-shadow:0 18px 40px rgba(0,0,0,.18)">
  <img style="display:block;width:100%;height:700px;object-fit:cover" src="{img}">
  <p style="position:absolute;left:0;right:0;bottom:36px;text-align:center;font:500 44px/1 {SERIF};color:#3A332B;margin:0">{_e(d.get("sub") or d.get("tag") or "")}</p>
</div>"""


def l_swiss(d, t, img):
    fs = _fs(d["title"], 900, 3, 80, 150)
    pts = _pts(d, 3)
    return f"""<div style="position:absolute;inset:0;background:{t['paper']}"></div>
<div style="position:absolute;left:0;top:0;width:100%;height:420px;background:{t['accent']}"></div>
<div style="position:absolute;left:80px;top:80px;font:700 36px/1 {SANS};color:{t['on']};letter-spacing:.14em">{_e(d.get("tag") or "GUIDE")}</div>
<div style="position:absolute;right:80px;top:80px;font:300 36px/1 {SANS};color:{t['on']}">{_e(d.get("vol") or "01")}</div>
<div style="position:absolute;left:80px;right:80px;top:470px">
  <h1 style="font:900 {fs}px/1.08 {SANS};color:{t['ink']};margin:0;letter-spacing:-.01em">{_e(d["title"])}</h1>
  <div style="margin-top:60px">{"".join(f'<div style="display:flex;gap:40px;border-top:2px solid {t["ink"]};padding:26px 0;font:500 44px/1.3 {SANS};color:{t["ink"]}"><span style="font-weight:300;color:{t["sub"]}">0{i + 1}</span><span>{_e(p)}</span></div>' for i, p in enumerate(pts))}</div>
</div>"""


LAYOUTS = {
    # key: (名称, 是否需要实拍图, 函数)
    "photo_top": ("大图压字", True, l_photo_top),
    "split_top": ("色块标题 + 下图", True, l_split_top),
    "split_bottom": ("上图 + 下方标题", True, l_split_bottom),
    "editorial": ("杂志封面", True, l_editorial),
    "sticker": ("贴纸标签", True, l_sticker),
    "polaroid": ("拍立得", True, l_polaroid),
    "statement": ("纯文字大字", False, l_statement),
    "memo": ("备忘录截图", False, l_memo),
    "checklist": ("清单卡", False, l_checklist),
    "big_number": ("大数字", False, l_big_number),
    "question": ("问句封面", False, l_question),
    "swiss": ("瑞士网格", False, l_swiss),
}

BASE_CSS = f"""*{{box-sizing:border-box}}html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden}}
body{{font-family:{SANS};-webkit-font-smoothing:antialiased}}
.c{{position:relative;width:{W}px;height:{H}px;overflow:hidden}}
.full{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}}
.pill{{display:inline-block;font:700 34px/1 {SANS};padding:16px 26px;border-radius:999px}}
h1{{overflow-wrap:anywhere;text-wrap:balance}}"""


def chromium() -> str:
    for c in ("chromium-headless-shell", "chromium", "chromium-browser", "google-chrome", "msedge"):
        p = shutil.which(c)
        if p:
            return p
    raise RuntimeError("服务器上没有找到 Chromium,无法渲染封面")


def build_html(layout: str, theme: str, data: dict, photo: str = "") -> str:
    t = THEMES.get(theme) or THEMES["cream"]
    _, need, fn = LAYOUTS[layout]
    if need and not photo:
        raise ValueError(f"版式「{layout}」需要一张实拍图")
    body = fn({**data, "title": data.get("title") or ""}, t, photo)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{BASE_CSS}</style></head>
<body><div class="c" style="background:{t['bg']}">{body}</div></body></html>"""


def render(layout: str, theme: str, data: dict, out: Path, photo: Path | None = None) -> Path:
    """渲染一张 1080×1440 的封面 PNG 到 out。photo 是本地实拍图(无字)。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = ""
        if photo:
            shutil.copy(photo, tmp / f"photo{Path(photo).suffix}")
            src = f"photo{Path(photo).suffix}"
        page = tmp / "card.html"
        page.write_text(build_html(layout, theme, data, src), encoding="utf-8")
        shot = tmp / "shot.png"
        cmd = [chromium(), "--headless", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--hide-scrollbars", "--force-device-scale-factor=1",
               f"--user-data-dir={tmp / 'profile'}", "--no-first-run", "--default-background-color=00000000",
               f"--window-size={W},{H}", f"--screenshot={shot}", page.as_uri()]
        r = subprocess.run(cmd, capture_output=True, timeout=60)
        if not shot.is_file():
            raise RuntimeError("封面渲染失败:" + (r.stderr.decode("utf-8", "ignore")[-200:] or "没有生成图片"))
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(shot, out)
    return out
