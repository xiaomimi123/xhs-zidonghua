"""产品:用户上传的产品图和资料。一键批量生成时,产品资料当作「作者提供的真实事实」写进笔记,
产品图当作参考图,保证配图里的产品和实物一致。"""
import base64
import json
import re
import secrets
from pathlib import Path

from . import mirror
from .db import q_all, q_exec, q_one

DIR = Path(__file__).resolve().parent.parent / "data" / "products"
DIR.mkdir(parents=True, exist_ok=True)
MAX_IMAGES, MAX_BYTES = 9, 4 * 1024 * 1024
DATA_URL = re.compile(r"^data:image/[a-zA-Z0-9.+-]+;base64,(.+)$", re.S)


def decode_images(data_urls: list[str]) -> list[tuple[bytes, str]]:
    """校验并解码上传的图片(页面已压缩成 JPEG 的 data URL)。按文件头判断真实类型,不收 SVG。"""
    if not data_urls:
        raise ValueError("请至少上传一张产品图")
    if len(data_urls) > MAX_IMAGES:
        raise ValueError(f"最多上传 {MAX_IMAGES} 张产品图")
    out = []
    for i, u in enumerate(data_urls, 1):
        m = DATA_URL.match(u or "")
        if not m:
            raise ValueError(f"第 {i} 张不是图片")
        try:
            b = base64.b64decode(m.group(1), validate=True)
        except ValueError:
            raise ValueError(f"第 {i} 张图片数据损坏")
        ext = mirror.sniff(b)
        if not ext:
            raise ValueError(f"第 {i} 张不是支持的图片格式(jpg / png / webp)")
        if len(b) > MAX_BYTES:
            raise ValueError(f"第 {i} 张图片太大(超过 {MAX_BYTES // 1024 // 1024}MB)")
        out.append((b, ext))
    return out


def create(name: str, data_urls: list[str], price: str = "", selling_points: str = "", audience: str = "", forbid: str = "") -> int:
    name = name.strip()[:40]
    if not name:
        raise ValueError("请填写产品名称")
    imgs = decode_images(data_urls)
    pid = q_exec("INSERT INTO products(name,price,selling_points,audience,forbid) VALUES(?,?,?,?,?)",
                 name, price.strip()[:40], selling_points.strip()[:500], audience.strip()[:100], forbid.strip()[:200]).lastrowid
    names = []
    for b, ext in imgs:
        n = f"p{pid}_{secrets.token_hex(4)}.{ext}"
        (DIR / n).write_bytes(b)
        names.append(n)
    q_exec("UPDATE products SET images=? WHERE id=?", json.dumps(names), pid)
    return pid


def update(pid: int, name: str, price: str = "", selling_points: str = "", audience: str = "", forbid: str = "",
           keep: list[str] | None = None, add: list[str] | None = None, order: list[str] | None = None):
    """改产品资料和图片。keep:保留的原图文件名;add:新上传的图(data URL)。至少留一张图。
    order:页面上的图片顺序,["k:文件名"] 是保留的原图,["a:0"] 是新上传的第几张;不传就是原图在前、新图在后。
    顺序很重要:生图时只取前几张当参考。换了图(或前 4 张的构成变了)就清掉上次的识别结果,下次生成会重新识别;只是调整顺序不清。"""
    p = get(pid)
    if not p:
        raise ValueError("找不到这个产品")
    name = name.strip()[:40]
    if not name:
        raise ValueError("请填写产品名称")
    kept = [i for i in p["images"] if i in set(keep or [])]
    new = decode_images(add) if add else []
    if not kept and not new:
        raise ValueError("至少保留一张产品图")
    if len(kept) + len(new) > MAX_IMAGES:
        raise ValueError(f"最多 {MAX_IMAGES} 张产品图")
    new_names = []
    for b, ext in new:
        n = f"p{pid}_{secrets.token_hex(4)}.{ext}"
        (DIR / n).write_bytes(b)
        new_names.append(n)
    names = []
    for t in order or []:   # 按页面顺序排;不认识的、重复的忽略
        kind, _, v = str(t).partition(":")
        if kind == "k" and v in kept and v not in names:
            names.append(v)
        elif kind == "a" and v.isdigit() and int(v) < len(new_names) and new_names[int(v)] not in names:
            names.append(new_names[int(v)])
    names += [n for n in [*kept, *new_names] if n not in names]   # order 里没提到的补在最后,不会丢图
    for old in p["images"]:
        if old not in kept:
            (DIR / old).unlink(missing_ok=True)
    changed = set(names) != set(p["images"]) or set(names[:4]) != set(p["images"][:4])
    q_exec("UPDATE products SET name=?, price=?, selling_points=?, audience=?, forbid=?, images=?" + (", profile='{}'" if changed else "") + " WHERE id=?",
           name, price.strip()[:40], selling_points.strip()[:500], audience.strip()[:100], forbid.strip()[:200], json.dumps(names), pid)


def get(pid: int | None) -> dict | None:
    return q_one("SELECT * FROM products WHERE id=?", pid) if pid else None


def paths(p: dict, n: int | None = None) -> list[Path]:
    out = [DIR / i for i in p.get("images") or [] if (DIR / i).is_file()]
    return out[:n] if n else out


def save_profile(pid: int, profile: dict):
    old = ((get(pid) or {}).get("profile") or {}).get("shoot")
    if old and "shoot" not in profile:   # 重新识别产品图时,保留已经生成的拍摄规格
        profile = {**profile, "shoot": old}
    q_exec("UPDATE products SET profile=?, category=? WHERE id=?", json.dumps(profile, ensure_ascii=False), profile.get("category") or "", pid)


def save_spec(pid: int, spec: dict):
    """只更新画像里的拍摄规格,不动品类等其他字段。"""
    prof = {**(((get(pid) or {}).get("profile")) or {}), "shoot": spec}
    q_exec("UPDATE products SET profile=? WHERE id=?", json.dumps(prof, ensure_ascii=False), pid)


def get_spec(p: dict | None, generate: bool = True) -> dict:
    """这个产品的拍摄规格:类型、冷热、真实搭配(带出现频率)、画面里不要出现的东西。缓存在产品画像里;
    没有就让 AI 按产品资料生成一次(以后新产品自动适配,不用人为每个产品单独调提示词)。
    没配 Key / 调用失败 / 产品换了图被清空:返回空规格,不缓存,下次再试;空规格时各处回退到原来的关键词规则。"""
    if not p:
        return {}
    prof = p.get("profile") or {}
    if prof.get("shoot"):
        return prof["shoot"]
    if not generate:
        return {}
    from . import llm
    try:
        spec = llm.shoot_spec(p["name"], p.get("category") or prof.get("category") or "", p.get("selling_points") or "", p.get("audience") or "",
                              prof.get("scenes"), prof.get("uses"), prof.get("appearance") or "")
    except Exception:
        return {}
    if spec.get("kind") or spec.get("pairings"):
        save_spec(p["id"], spec)
        p["profile"] = {**prof, "shoot": spec}
    return spec


PAIR_FREQ_TEXT = {"high": "常用", "mid": "偶尔", "low": "少用,整篇最多一张"}


def avoid_text(spec: dict | None) -> str:
    """生图提示词里「不要出现」的那一句:规格里列的 + 不是热饮就不要热气。没有规格返回空串。"""
    spec = spec or {}
    items = list(spec.get("avoid") or [])
    if spec.get("serve") and spec["serve"] != "hot" and not any(("热气" in x or "蒸汽" in x or "冒烟" in x) for x in items):
        items.append("热气和蒸汽")
    return ("不要出现:" + "、".join(items)) if items else ""


def listing() -> list[dict]:
    return [{"id": p["id"], "name": p["name"], "category": p["category"], "price": p["price"],
             "selling_points": p["selling_points"], "audience": p["audience"], "forbid": p["forbid"],
             "images": [f"/product_images/{i}" for i in p["images"]], "created": p["created"]}
            for p in q_all("SELECT * FROM products ORDER BY id DESC")]


def delete(pid: int):
    p = get(pid)
    if p:
        for f in paths(p):
            f.unlink(missing_ok=True)
        q_exec("DELETE FROM products WHERE id=?", pid)


def block(p: dict | None) -> str:
    """写作和规划角度时附带的产品资料。"""
    if not p:
        return ""
    prof = p.get("profile") or {}
    rows = [("产品名", p["name"]), ("品类", p.get("category") or prof.get("category")), ("卖点(作者提供)", p.get("selling_points")),
            ("价格", p.get("price")), ("适合人群", p.get("audience")), ("外观(从产品图识别)", prof.get("appearance")),
            ("包装上的字", prof.get("package_text")), ("可以带出产品的场景", "、".join(prof.get("scenes") or []))]
    spec = get_spec(p)
    if spec:
        pairs = "、".join(f"{x['name']}({PAIR_FREQ_TEXT.get(x['freq'], '偶尔')})" for x in spec.get("pairings") or [])
        rows += [("拍摄规格·真实的搭配和吃法(括号是出现频率)", pairs), ("拍摄规格·画面里不要出现", "、".join(spec.get("avoid") or [])),
                 ("拍摄规格·冷热", {"hot": "热饮,杯口可以有一点热气,别的东西不要冒热气", "cold": "冷食,画面里不要有热气蒸汽", "room": "常温食品,画面里不要有热气蒸汽"}.get(spec.get("serve") or "")),
                 ("拍摄规格·提示", spec.get("shoot_note"))]
    lines = [f"- {k}:{v}" for k, v in rows if v]
    forbid = f"\n- 不能出现的说法:{p['forbid']}" if p.get("forbid") else ""
    return ("\n\n【产品资料】以下是作者提供的真实产品资料,可以直接写进笔记;不要超出这些资料编造功效、成分、销量、价格。\n"
            + "\n".join(lines) + forbid +
            "\n要求:产品要自然植入——先讲读者关心的场景或问题,再带出产品,不要写成硬广;购买渠道只能说本平台内;"
            "配图提示词里用「图中的产品」指代它,只写场景和构图,不要自己描述产品外观(以参考图为准)。"
            "配图和封面的场景必须符合这个产品真实的使用方式(有拍摄规格的以拍摄规格为准):冲泡饮品(咖啡、茶、奶类)放在餐桌、咖啡角、办公桌、茶几、窗边,不要放在灶台、炒锅、油烟重的厨房;"
            "零食点心放在桌面、沙发、办公桌;只有烹饪调味类产品才出现厨房灶台。"
            "同一篇里几张配图的搭配、场景、拍摄角度要各不相同;拍摄规格里标「少用」的搭配整篇最多一张,常见搭配也不要每张都一样;"
            "画面整洁自然,最多有一两件日常小物,不要写纸巾团、水渍、杂物堆;除热饮外不要写热气、冒烟、热腾腾、刚出锅这类会让画面冒烟的词。")
