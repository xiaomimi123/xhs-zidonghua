"""小红书网页抓取(Playwright)。只负责「拿到笔记」,不关心工作台协议。

策略:搜索页/主页只取笔记卡片链接(自带 xsec_token),再逐条打开详情页,
优先读 window.__INITIAL_STATE__ 里的结构化数据,读不到再退回读 DOM。
页面结构会变,选择器集中放在本文件顶部附近,改版时只改这里。
"""
import json
import random
import re
import time
from datetime import datetime, timedelta
from urllib.parse import quote

BASE = "https://www.xiaohongshu.com"
NOTE_ID_RE = re.compile(r"/(?:explore|search_result|discovery/item|user/profile/[0-9a-zA-Z]+)/([0-9a-f]{24})")
USER_ID_RE = re.compile(r"/user/profile/([0-9a-zA-Z]+)")

SORT_LABEL = {"popularity": "最热", "latest": "最新", "general": "综合"}


class CollectError(Exception):
    """可上报给工作台的失败原因(会原样显示给用户)。"""


def nap(a=1.5, b=3.5):
    time.sleep(random.uniform(a, b))


def _num(v):
    """页面上的计数可能是 '1.2万' / '' / None,原样交给工作台换算,空的不传。"""
    return v if v not in (None, "") else None


def _fmt_date(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d")
    except Exception:
        return ""


# ---------- 登录 / 风控检测 ----------

def check_blocked(page):
    """遇到验证码、登录墙就抛 CollectError,让任务带着原因失败,用户可重试。"""
    url = page.url
    if "captcha" in url or "verify" in url:
        raise CollectError("触发验证码/安全验证,请在采集端浏览器里手动通过后重试")
    try:
        txt = page.locator("body").inner_text(timeout=3000)
    except Exception:
        return
    if "请通过验证" in txt or "滑动验证" in txt or "安全验证" in txt[:400]:
        raise CollectError("触发验证码/安全验证,请在采集端浏览器里手动通过后重试")
    if page.locator(".login-container, .login-modal").count() and page.locator(".login-container, .login-modal").first.is_visible():
        raise CollectError("小红书登录已失效,需要在采集端重新登录(运行 login.bat)")


def is_logged_in(page):
    try:
        return bool(page.evaluate("""() => {
            const u = window.__INITIAL_STATE__ && window.__INITIAL_STATE__.user;
            const info = u && (u.userInfo && (u.userInfo._rawValue || u.userInfo._value || u.userInfo));
            return !!(info && info.user_id);
        }"""))
    except Exception:
        return False


# ---------- 列表页:收集笔记卡片 ----------

_CARD_JS = """() => {
  const out = [];
  document.querySelectorAll('section.note-item, .note-item').forEach(card => {
    const a = card.querySelector('a[href*="/explore/"], a[href*="/search_result/"], a.cover');
    if (!a) return;
    const likeEl = card.querySelector('.like-wrapper .count, .count');
    out.push({
      href: a.href,
      likes: likeEl ? likeEl.innerText.trim() : '',
      video: !!card.querySelector('.play-icon, .video-icon, [class*="play"]'),
    });
  });
  return out;
}"""


def collect_cards(page, want, max_screens=8):
    """滚动列表页,返回去重后的卡片 [{id, href, likes, video}],最多 want 张。"""
    seen, cards = set(), []
    for _ in range(max_screens):
        for c in page.evaluate(_CARD_JS):
            m = NOTE_ID_RE.search(c["href"])
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            cards.append({"id": m.group(1), **c})
        if len(cards) >= want:
            break
        page.mouse.wheel(0, random.randint(900, 1400))
        nap(1.2, 2.4)
    return cards[:want]


def _click_filter(page, label):
    """搜索页筛选面板:悬停「筛选」再点选项。找不到就算了(降级为默认排序),不影响采集。"""
    try:
        page.get_by_text("筛选", exact=True).first.hover(timeout=3000)
        nap(0.5, 1)
        page.get_by_text(label, exact=True).first.click(timeout=3000)
        nap(1.5, 2.5)
        return True
    except Exception:
        return False


def search_cards(page, query, want, sort="popularity", note_type="image"):
    page.goto(f"{BASE}/search_result?keyword={quote(query)}&source=web_search_result_notes", wait_until="domcontentloaded")
    nap(2.5, 4)
    check_blocked(page)
    if sort in SORT_LABEL and sort != "general":
        _click_filter(page, SORT_LABEL[sort])
    if note_type == "image":
        _click_filter(page, "图文")
    cards = collect_cards(page, want)
    if not cards:
        raise CollectError("搜索无结果(或页面结构变化,选择器未命中)")
    return cards


def resolve_author_url(page, query):
    """博主任务的 query 可能是主页链接或小红书号。返回主页 URL。"""
    q = query.strip()
    if "xiaohongshu.com/user/profile/" in q:
        return q
    page.goto(f"{BASE}/search_result?keyword={quote(q)}&type=51", wait_until="domcontentloaded")
    nap(2.5, 4)
    check_blocked(page)
    href = page.evaluate("() => { const a = document.querySelector('a[href*=\"/user/profile/\"]'); return a ? a.href : ''; }")
    if not href:
        raise CollectError("博主不存在或搜不到该小红书号")
    return href


def author_cards(page, query, want):
    url = resolve_author_url(page, query)
    page.goto(url, wait_until="domcontentloaded")
    nap(2.5, 4)
    check_blocked(page)
    body = page.locator("body").inner_text()
    if "账号不存在" in body or "已注销" in body or "无法浏览" in body:
        raise CollectError("博主不存在或账号已注销")
    if "私密" in body[:600] and "笔记" not in body[:600]:
        raise CollectError("博主账号私密,无法查看")
    # 候选池取 want*2,详情页读完后按点赞排序取前 want 篇
    cards = collect_cards(page, min(want * 2, 40))
    if not cards:
        raise CollectError("博主主页没有可见笔记")
    return cards


# ---------- 详情页 ----------

_STATE_JS = """(id) => {
  const s = window.__INITIAL_STATE__;
  if (!s || !s.note) return null;
  const unwrap = v => (v && (v._rawValue || v._value)) || v;
  const map = unwrap(s.note.noteDetailMap) || {};
  const hit = map[id] || Object.values(map)[0];
  const n = hit && unwrap(hit.note);
  return n ? JSON.stringify(n) : null;
}"""


def read_note(page, card):
    """打开一张卡片对应的详情页,返回工作台格式的笔记 dict;读不到正文返回 None。"""
    page.goto(card["href"], wait_until="domcontentloaded")
    nap(2, 4)
    check_blocked(page)
    raw = None
    for _ in range(4):  # 状态数据在水合后才有
        raw = page.evaluate(_STATE_JS, card["id"])
        if raw:
            break
        time.sleep(1)
    note = _from_state(json.loads(raw), card) if raw else _from_dom(page, card)
    if not note or not note.get("content", "").strip():
        return None
    return note


def _from_state(n, card):
    user = n.get("user") or {}
    inter = n.get("interactInfo") or {}
    images = []
    for im in n.get("imageList") or []:
        u = im.get("urlDefault") or im.get("urlPre") or ""
        if not u and im.get("infoList"):
            u = im["infoList"][-1].get("url", "")
        if u:
            images.append("https:" + u if u.startswith("//") else u)
    nid = n.get("noteId") or card["id"]
    uid = user.get("userId") or ""
    return {
        "note_id": nid,
        "url": f"{BASE}/explore/{nid}",
        "title": (n.get("title") or "").strip(),
        "content": (n.get("desc") or "").strip(),
        "author": user.get("nickname") or user.get("nickName") or "",
        "author_id": uid,
        "author_url": f"{BASE}/user/profile/{uid}" if uid else "",
        "likes": _num(inter.get("likedCount")),
        "collects": _num(inter.get("collectedCount")),
        "comments": _num(inter.get("commentCount")),
        "images": images,
        "cover": images[0] if images else "",
        "tags": [t.get("name", "") for t in (n.get("tagList") or []) if t.get("name")],
        "type": "video" if n.get("type") == "video" else "normal",
        "published_at": _fmt_date(n.get("time")),
    }


def _from_dom(page, card):
    """状态数据读不到时的退路。互动数、图片以 DOM 为准,字段可能不全。"""
    def txt(sel):
        loc = page.locator(sel)
        return loc.first.inner_text().strip() if loc.count() else ""
    content = txt("#detail-desc .note-text, #detail-desc")
    if not content:
        return None
    # 话题标签在正文里是 #xxx[话题]#,按约定正文不含话题
    tags = re.findall(r"#([^#\[\s]+)\[话题\]#", content)
    content = re.sub(r"#[^#\[\s]+\[话题\]#", "", content).strip()
    imgs = page.evaluate("""() => [...document.querySelectorAll('.swiper-slide:not(.swiper-slide-duplicate) img, .note-slider img')]
        .map(i => i.src).filter(s => s && s.startsWith('http'))""")
    return {
        "note_id": card["id"], "url": f"{BASE}/explore/{card['id']}",
        "title": txt("#detail-title"), "content": content,
        "author": txt(".author-wrapper .username, .author-container .username"),
        "likes": _num(txt(".engage-bar .like-wrapper .count")),
        "collects": _num(txt(".engage-bar .collect-wrapper .count")),
        "comments": _num(txt(".engage-bar .chat-wrapper .count")),
        "images": list(dict.fromkeys(imgs)), "cover": imgs[0] if imgs else "",
        "tags": tags, "type": "video" if page.locator("video").count() else "normal",
        "published_at": txt(".bottom-container .date"),
    }


def too_old(note, max_age_days):
    if not max_age_days or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", note.get("published_at", "")):
        return False
    return datetime.strptime(note["published_at"], "%Y-%m-%d") < datetime.now() - timedelta(days=max_age_days)
