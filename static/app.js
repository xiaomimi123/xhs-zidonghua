// 服务器时间(UTC,形如 2026-11-03 07:02:00)→ 浏览器本地时间。kind: "md" 月-日 时:分(默认)/ "ymd" 年-月-日 / "d" 月-日
const lt = (s, kind = "md") => {
  const d = new Date(String(s || "").replace(" ", "T") + "Z"); if (!s || isNaN(d)) return String(s || "").slice(kind === "ymd" ? 0 : 5, kind === "ymd" ? 10 : 16);
  const p = n => String(n).padStart(2, "0"), ymd = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  return kind === "ymd" ? ymd : kind === "d" ? ymd.slice(5) : `${ymd.slice(5)} ${p(d.getHours())}:${p(d.getMinutes())}`;
};
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
// GET 请求:同一时刻相同地址的请求只发一次;几乎不变的接口(赛道、动作模板、账号列表…)缓存 30 秒;任何写操作都会清掉缓存
const _inflight = new Map(), _cache = new Map();
const CACHEABLE = /^\/api\/(tracks|live\/(motions|scenes|bg_options)|qianfan\/(accounts|hot))(\?|$)/;
const _fetchJson = async (url, method, body) => {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  if (r.status === 401) { gate(); throw new Error("需要输入访问密码"); }
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m } catch { } throw new Error(typeof m === "string" ? m : JSON.stringify(m)); }
  return r.json();
};
const api = async (url, method = "GET", body) => {
  if (method !== "GET") { _cache.clear(); return _fetchJson(url, method, body); }
  const hit = _cache.get(url); if (hit && Date.now() - hit.t < 30000) return structuredClone(hit.v);
  let run = _inflight.get(url);
  if (!run) {
    run = _fetchJson(url, "GET");
    _inflight.set(url, run);
    run.then(v => { if (CACHEABLE.test(url)) _cache.set(url, { t: Date.now(), v }); }, () => { }).finally(() => _inflight.delete(url));
  }
  return structuredClone(await run);   // 每个调用方拿自己的一份,改了不会影响别人
};
// 复制到剪贴板:http 局域网地址下 navigator.clipboard 不可用,退回到隐藏文本框 + execCommand
async function copyText(t) {
  try { if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(t); return; } } catch {}
  const ta = document.createElement("textarea");
  ta.value = t; ta.setAttribute("readonly", ""); ta.style.cssText = "position:fixed;top:0;left:0;opacity:0";
  document.body.appendChild(ta); ta.select(); ta.setSelectionRange(0, t.length);
  let ok = false; try { ok = document.execCommand("copy"); } catch {}
  ta.remove();
  if (!ok) throw new Error("copy failed");
}
const toast = (msg, err) => {
  $$(".toast").forEach(t => t.remove());
  const t = document.createElement("div"); t.className = "toast" + (err ? " err" : ""); t.textContent = msg;
  document.body.appendChild(t); setTimeout(() => t.remove(), 2600);
};
// 列表 / 卡片网格里用缩略图(WebP,几十 KB),只有点开预览才加载原图;外链图片原样返回
const thumb = (u, w = 360) => u && /^\/(images|note_images|product_images)\//.test(u) && !u.includes("?") ? `${u}?w=${w}` : u;
// 分页条:‹ 上一页  1 … 4 5 [6] 7 8 … 20  下一页 ›(最多显示 7 个页码)。按钮带 data-pg,由页面自己监听点击
function pager(page, total, size, attr = "pg") {
  const pages = Math.max(1, Math.ceil(total / size));
  if (pages <= 1) return "";
  const nums = new Set([1, pages, page - 1, page, page + 1]);
  if (page <= 3) [2, 3, 4].forEach(n => nums.add(n));
  if (page >= pages - 2) [pages - 1, pages - 2, pages - 3].forEach(n => nums.add(n));
  const list = [...nums].filter(n => n >= 1 && n <= pages).sort((a, b) => a - b);
  const btn = (n, label, extra = "") => `<button class="pg ${extra}" data-${attr}="${n}" ${extra.includes("dis") ? "disabled" : ""} ${n === page && !extra ? 'aria-current="page"' : ""} aria-label="${label}">${label}</button>`;
  return `<nav class="pager" aria-label="分页">${btn(page - 1, "‹ 上一页", page <= 1 ? "dis" : "")}${list.map((n, i) => (i && n - list[i - 1] > 1 ? `<span class="pg-gap" aria-hidden="true">…</span>` : "") + btn(n, n, n === page ? "on" : "")).join("")}${btn(page + 1, "下一页 ›", page >= pages ? "dis" : "")}</nav>`;
}
const fmt = n => n >= 10000 ? (n / 10000).toFixed(1) + "w" : (n ?? 0);

const STEPS = [
  ["track", "选赛道", "选一个赛道,再定关键词和想做的内容方向"],
  ["crawl", "采集", "采集端按关键词搜索小红书,把爆款笔记回传到这里"],
  ["analyze", "拆解", "选几篇爆款,让 DeepSeek 拆出可复用的写作套路"],
  ["generate", "复刻", "按拆解卡批量写出同风格的原创笔记,并自动配图"],
  ["export", "导出", "勾选成品,打成 ZIP 去手动发布"],
];
let TRACKS = []; // [名称, 推荐关键词[]],由管理端配置
const TYPE_NAME = { keyword: "关键词", title: "标题", author: "博主" };
const TYPE_HINT = { keyword: "追加一个关键词", title: "粘贴一篇爆款笔记的标题,找同类笔记", author: "博主主页链接或小红书号,采集 TA 的笔记" };
const ST = {
  pending: "等待领取", running: "采集中", done: "已完成", failed: "失败",
  queued: "排队中", writing: "写作中", reviewing: "审稿中", drawing: "生图中", text_done: "文案已出", ready: "已就绪", error: "出错", exported: "已导出", new: "待生成",
};
const ST_CLS = { pending: "", running: "run", done: "done", failed: "error", queued: "", writing: "run", reviewing: "run", drawing: "run", text_done: "text_done", ready: "ready", error: "error", exported: "exported" };
const st = s => `<span class="st ${ST_CLS[s] ?? ""}">${ST[s] || esc(s)}</span>`;
const AUTO_ST = { profiling: "识别产品", collecting: "采集中", tagging: "分类中", analyzing: "拆解中", writing: "生成中", retrying: "质检中", done: "完成", error: "出错", canceled: "已取消" };
const AUTO_STEPS = [["profiling", "识别产品"], ["collecting", "采集"], ["tagging", "分类"], ["analyzing", "拆解"], ["writing", "写稿配图"], ["retrying", "质检"], ["done", "完成"]];
const BUSY = ["queued", "writing", "reviewing", "drawing", "running", "pending"];

const state = {
  tab: localStorage.tab || "new", pid: +localStorage.pid || null, projects: [], runId: +localStorage.runId || null, advOpen: false,
  picked: new Set(), ready: new Set(), pbSel: null, draft: { track: "", keywords: [], direction: "" },
};
const project = () => state.projects.find(p => p.id === state.pid);
let pollT;
const loop = (fn, ms) => {
  const tab = state.tab;
  const tick = async () => { if (state.tab !== tab) return; let again = false; try { again = await fn(); } catch { } if (again && state.tab === tab) pollT = setTimeout(tick, ms); };
  clearTimeout(pollT); pollT = setTimeout(tick, ms);
};

async function loadProjects() {
  state.projects = await api("/api/projects");
  if (!project()) state.pid = state.projects[0]?.id || null;
  localStorage.pid = state.pid || "";
}
const setProject = id => { if (id !== state.pid) { state.picked.clear(); state.ready.clear(); state.pbSel = null; } state.pid = id; localStorage.pid = id; };

// ---------- 顶栏 ----------
const RUNNING = ["profiling", "collecting", "tagging", "analyzing", "writing", "retrying"];
function nav() {
  const fv = window.FLOW_VIEWS?.[state.tab], adv = !fv || state.advOpen;
  document.body.classList.toggle("adv", adv); $("#advt").setAttribute("aria-expanded", adv);
  $$("#fnav [data-k], #tabbar [data-k]").forEach(b => b.classList.toggle("on", !!fv && b.dataset.k === (fv.nav || state.tab)));
  const rn = state.projects.filter(x => RUNNING.includes(x.auto_status)).length;
  for (const id of ["#runN", "#runN2"]) { $(id).textContent = rn; $(id).hidden = !rn; }
  const p = project();
  const badge = k => k === "crawl" && p?.crawling ? `<em class="spin">${p.crawling}</em>` : k === "analyze" && state.picked.size ? `<em>${state.picked.size}</em>` : k === "export" && state.ready.size ? `<em>${state.ready.size}</em>` : "";
  $("#nav").innerHTML = STEPS.slice(0, 5).map(([k, n], i) => `${i ? '<span class="sep"></span>' : ""}<button class="${k === state.tab ? "on" : ""}" data-k="${k}"><i>${i + 1}</i><span>${n}</span>${badge(k)}</button>`).join("");
  $("#proj").classList.toggle("none", !p);
  $("#proj").innerHTML = p ? `<span>${esc(p.track)}</span><b>${esc(p.keywords.join("、"))}</b><s>▾</s>` : `<span>项目</span><b>未选择赛道</b><s>▾</s>`;
  if (fv) { $("#pe").textContent = ""; $("#pt").textContent = fv.t; $("#pd").textContent = fv.d; document.title = fv.t + " · 小红书流水线"; return; }
  const i = STEPS.findIndex(x => x[0] === state.tab), cur = STEPS[i];
  $("#pe").textContent = `STEP 0${i + 1} / 05`;
  $("#pt").textContent = cur[1]; $("#pd").textContent = cur[2];
  document.title = cur[1] + " · 小红书流水线";
}
const go = k => { state.tab = k; localStorage.tab = k; render(); window.scrollTo(0, 0); };
function projPop() {
  const old = $(".pop"); if (old) return old.remove();
  const el = document.createElement("div"); el.className = "pop";
  el.innerHTML = `${state.projects.map(p => `<button data-pid="${p.id}" class="${p.id === state.pid ? "on" : ""}"><b>${esc(p.track)} · ${esc(p.keywords.join("、"))}</b><span class="sub">${p.note_count} 篇笔记 · ${p.draft_count} 篇草稿 · ${esc(lt(p.created))}</span></button>`).join("") || `<div class="sub" style="padding:10px 12px">还没有项目</div>`}
    <button class="new" data-new>＋ 新建赛道</button>`;
  $("#proj").after(el);
}
// 草稿配图:点击放大查看,左右键/按钮切换,Esc 或点空白处关闭
function lightbox(srcs, i) {
  const m = document.createElement("div"); m.className = "lb";
  const show = () => { m.innerHTML = `<img src="${esc(srcs[i])}"><span class="lb-n">${i + 1} / ${srcs.length}</span>${srcs.length > 1 ? `<button class="lb-b lb-p" data-d="-1">‹</button><button class="lb-b lb-x" data-d="1">›</button>` : ""}<button class="lb-c" data-c>✕</button>`; };
  const step = d => { i = (i + d + srcs.length) % srcs.length; show(); };
  const close = () => { m.remove(); document.removeEventListener("keydown", key); };
  const key = e => { if (e.key === "Escape") close(); else if (e.key === "ArrowLeft") step(-1); else if (e.key === "ArrowRight") step(1); };
  m.onclick = e => { const b = e.target.closest("[data-d]"); if (b) return step(+b.dataset.d); if (e.target.tagName !== "IMG") close(); };
  document.addEventListener("keydown", key); show(); document.body.appendChild(m);
}
document.addEventListener("click", e => {
  const xv = e.target.closest(".xr .xinfo");  // 点标题:查看文案(不勾选这一行)
  if (xv) { e.preventDefault(); return openDraft(+xv.closest(".xr").querySelector("input").dataset.id); }
  const xt = e.target.closest(".xr img.xthumb");  // 导出列表的缩略图:放大该草稿的全部配图,不勾选/取消勾选这一行
  if (xt) { e.preventDefault(); return lightbox(JSON.parse(xt.dataset.imgs), 0); }
  const im = e.target.closest(".dc-media img"); if (!im) return;
  const all = [...im.closest(".dc-media").querySelectorAll("img")];
  lightbox(all.map(x => x.getAttribute("src")), all.indexOf(im));
});
document.addEventListener("click", e => {
  const fk = e.target.closest("#fnav [data-k], #tabbar [data-k]"); if (fk) { state.advOpen = false; return go(fk.dataset.k); }
  if (e.target.closest("#advt")) { if (!window.FLOW_VIEWS?.[state.tab]) { state.advOpen = false; return go("new"); } state.advOpen = !state.advOpen; return nav(); }
  const b = e.target.closest("#nav button"); if (b) return go(b.dataset.k);
  if (e.target.closest("#proj")) return projPop();
  const pp = e.target.closest(".pop [data-pid]"); if (pp) { setProject(+pp.dataset.pid); $(".pop").remove(); return go(state.tab === "track" ? "crawl" : state.tab); }
  if (e.target.closest(".pop [data-new]")) { $(".pop").remove(); return go("track"); }
  if (!e.target.closest(".pop")) $(".pop")?.remove();
  const g = e.target.closest("[data-go]"); if (g) go(g.dataset.go);
});
async function render() {
  document.body.classList.remove("fl-picking");
  clearTimeout(pollT);
  // 项目列表只有高级手动流程和侧栏的「进行中」角标要用:新版页面不等它,和页面自己的数据一起并行取,少一次串行往返
  const projP = loadProjects().catch(() => { });
  const flow = !!window.FLOW_VIEWS?.[state.tab];
  if (!flow) await projP;
  nav(); $("#pa").innerHTML = "";
  if (!window.FLOW_VIEWS?.[state.tab] && !state.advOpen) state.tab = "new";   // 高级手动流程的入口已隐藏:刷新后不要停在它上面
  const fv = window.FLOW_VIEWS?.[state.tab];
  if (fv) {
    const seq = state.renderSeq = (state.renderSeq || 0) + 1;
    document.body.classList.add("busy");   // 顶部进度条 + 旧内容变淡:点了马上有反馈
    projP.then(() => { if (state.renderSeq === seq) nav(); });   // 项目列表回来后刷新侧栏角标
    try { return await fv.fn(); } finally { if (state.renderSeq === seq) document.body.classList.remove("busy"); }
  }
  if (!STEPS.some(x => x[0] === state.tab)) { state.tab = "new"; return window.FLOW_VIEWS.new.fn(); }
  const needP = ["crawl", "analyze", "generate", "export"].includes(state.tab);
  if (needP && !project()) {
    $("#main").innerHTML = `<div class="empty"><b>还没有选赛道</b>先选一个赛道并填写关键词,工作台会自动生成采集任务<br><button class="btn" data-go="track">去选赛道</button></div>`;
    return;
  }
  ({ track: vTrack, crawl: vCrawl, analyze: vAnalyze, generate: vGenerate, export: vExport })[state.tab]();
}

// ---------- 1 选赛道 ----------
const INTENT = /教程|做法|怎么|如何|攻略|推荐|测评|评测|避坑|清单|自制|在家|技巧|方法|食谱|步骤|合集|对比|入门|新手|好物|种草|体验|心得|分享|大全|公式|搭配|吃法|冲泡|冲调|制作/;
const isBroad = w => w.length >= 2 && w.length <= 6 && !INTENT.test(w);  // 只有一个食材/品类名,没有「做法、教程」这类意图
const kwSuggest = async (word, track) => { try { return await api("/api/keywords/suggest", "POST", { track: track || "", word }); } catch { return { broad: false, suggestions: [] }; } };
const sugHtml = (word, r, attr) => `<div class="sug"><div class="sug-h"><b>「${esc(word)}」比较宽泛</b><span class="sub">容易搜到不相关的内容(比如搜「咖啡」会搜到咖啡店、兼职)。试试更具体的词,点一下就换:</span></div>
  <div class="sug-l">${r.suggestions.map(x => `<button class="chip" ${attr}="${esc(x.word)}" title="${esc(x.hint)}">${esc(x.word)}${x.hint ? `<small>${esc(x.hint)}</small>` : ""}</button>`).join("")}</div>
  <div class="sug-f"><button class="btn ghost sm" data-sugkeep>就用「${esc(word)}」</button></div></div>`;
const modeBar = () => `<div class="seg"><button class="${state.mode === "product" ? "" : "on"}" data-mode="track">按赛道生成</button><button class="${state.mode === "product" ? "on" : ""}" data-mode="product">我有产品</button></div>`;
const shrink = file => new Promise((ok, no) => {  // 产品图在浏览器里压成 ≤1600px 的 JPEG 再上传
  const u = URL.createObjectURL(file), im = new Image();
  im.onload = () => { const k = Math.min(1, 1600 / Math.max(im.width, im.height)), c = document.createElement("canvas"); c.width = Math.round(im.width * k); c.height = Math.round(im.height * k); c.getContext("2d").drawImage(im, 0, 0, c.width, c.height); URL.revokeObjectURL(u); ok(c.toDataURL("image/jpeg", 0.86)); };
  im.onerror = () => { URL.revokeObjectURL(u); no(new Error("图片读取失败:" + file.name)); };
  im.src = u;
});
async function vProduct() {
  const P = state.prod ||= { name: "", files: [], price: "", sp: "", aud: "", forbid: "", track: "", pick: 0 };
  const list = await api("/api/products").catch(() => []), picked = P.pick && list.find(x => x.id === P.pick);
  const sync = () => { if (picked) return; for (const [k, id] of [["name", "pname"], ["price", "pprice"], ["sp", "psp"], ["aud", "paud"], ["forbid", "pforbid"]]) if ($("#" + id)) P[k] = $("#" + id).value; if ($("#ptrack")) P.track = $("#ptrack").value; };
  $("#main").innerHTML = `${modeBar()}<section class="card">
    <div class="sec-h"><span class="no">1</span><b>产品</b><span class="sub">上传产品图、写上名称,赛道、关键词和文案角度由系统自动完成</span></div>
    ${list.length ? `<div class="sugs"><span class="sub">已有产品:</span>${list.map(x => `<button class="chip ${P.pick === x.id ? "on" : ""}" data-pick="${x.id}">${esc(x.name)}</button>`).join("")}${P.pick ? `<button class="chip" data-pick="0">+ 新产品</button>` : ""}</div>` : ""}
    ${picked ? `<div class="pthumbs">${picked.images.map(u => `<span class="pt"><img src="${esc(u)}"></span>`).join("")}</div>
      <div class="sub">${esc(picked.name)}${picked.price ? " · " + esc(picked.price) : ""}${picked.selling_points ? " · 卖点:" + esc(picked.selling_points) : ""}</div>`
    : `<input id="pname" placeholder="产品名称,如:XX 牌红腐乳" value="${esc(P.name)}" style="max-width:420px">
      <div class="pthumbs">${P.files.map((u, i) => `<span class="pt"><img src="${u}"><button data-prm="${i}" aria-label="移除">×</button></span>`).join("")}<label class="padd">+ 上传产品图<input id="pfile" type="file" accept="image/*" multiple hidden></label></div>
      <div class="sub">建议 2–6 张,包装正面最重要;系统会自动压缩</div>
      <details style="margin-top:12px"><summary class="sub" style="cursor:pointer">补充资料(可选,写得越准,文案越贴合)</summary>
        <div class="two" style="margin-top:10px"><div><label class="f">价格</label><input id="pprice" value="${esc(P.price)}" placeholder="如:29.9 元 / 500g"></div>
        <div><label class="f">适合人群</label><input id="paud" value="${esc(P.aud)}" placeholder="如:爱吃早餐的上班族"></div></div>
        <label class="f">产品卖点</label><textarea id="psp" rows="2" placeholder="如:古法发酵 · 无添加防腐剂 · 小罐装">${esc(P.sp)}</textarea>
        <label class="f">不能出现的说法</label><input id="pforbid" value="${esc(P.forbid)}" placeholder="如:不写疗效、不提竞品"></details>`}
  </section>
  <section class="card" style="margin-top:16px"><div class="row" style="gap:10px;justify-content:flex-end;flex-wrap:wrap">
    <label class="sub">赛道 <select id="ptrack" style="width:auto;display:inline-block"><option value="">自动识别</option>${TRACKS.map(([t]) => `<option ${P.track === t ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label>
    <label class="sub">篇数 <input id="acount" type="number" min="1" max="50" value="${state.acount || 10}" style="width:70px;display:inline-block"></label>
    <button class="btn lg" id="auto">一键生成 →</button></div></section>`;
  $("#main").onclick = async e => {
    const mb = e.target.closest("[data-mode]"); if (mb) { sync(); state.mode = mb.dataset.mode; return vTrack(); }
    const pk = e.target.closest("[data-pick]"); if (pk) { sync(); P.pick = +pk.dataset.pick; return vProduct(); }
    const rm = e.target.closest("[data-prm]"); if (rm) { sync(); P.files.splice(+rm.dataset.prm, 1); return vProduct(); }
  };
  $("#pfile") && ($("#pfile").onchange = async e => {
    sync(); const fs = [...e.target.files];
    try { for (const f of fs) if (P.files.length < 9) P.files.push(await shrink(f)); } catch (err) { toast(err.message, 1); }
    vProduct();
  });
  $("#auto").onclick = async () => {
    sync();
    if (!picked && !P.name.trim()) return toast("请填写产品名称", 1);
    if (!picked && !P.files.length) return toast("请上传至少一张产品图", 1);
    const count = Math.max(1, Math.min(+$("#acount").value || 10, 50)); state.acount = count;
    $("#auto").disabled = true;
    try {
      const body = { track: P.track, count, ...(picked ? { product_id: picked.id } : { product: { name: P.name, images: P.files, price: P.price, selling_points: P.sp, audience: P.aud, forbid: P.forbid } }) };
      const r = await api("/api/auto", "POST", body);
      setProject(r.id); state.prod = null; toast(`已开始:${count} 篇,先识别产品图`); openRun(r.id);
    } catch (e) { toast(e.message, 1); $("#auto").disabled = false; }
  };
}
async function vTrack() {
  if (!TRACKS.length) TRACKS = (await api("/api/tracks")).map(t => [t.name, t.keywords]);
  if (state.mode === "product") return vProduct();
  const d = state.draft, preset = TRACKS.find(t => t[0] === d.track);
  const custom = d.track && !preset;
  const kwSugHtml = () => { const g = state.kwSug; return g && d.keywords.includes(g.word) && g.r.suggestions.length ? sugHtml(g.word, g.r, "data-sugpick") : ""; };
  $("#main").innerHTML = `${modeBar()}<section class="card">
    <div class="sec-h"><span class="no">1</span><b>选择赛道</b></div>
    <div class="tracks">${TRACKS.map(([t, ks]) => `<button class="trk ${t === d.track ? "on" : ""}" data-t="${t}"><b>${t}</b><span>${ks.slice(0, 3).join(" · ")}</span></button>`).join("")}
      <button class="trk ${custom || d.track === "__other" ? "on" : ""}" data-t="__other"><b>其他赛道</b><span>自定义填写</span></button></div>
    ${custom || d.track === "__other" ? `<input id="tcustom" style="margin-top:12px;max-width:320px" placeholder="输入赛道名称,如:汉服" value="${custom ? esc(d.track) : ""}">` : ""}
  </section>
  <section class="card ${d.track ? "" : "dim"}" style="margin-top:16px">
    <div class="sec-h"><span class="no">2</span><b>关键词</b><span class="sub">采集端会按每个关键词分别搜索,最多 5 个</span></div>
    <div class="kwbox"><span id="kwtoks">${d.keywords.map((k, i) => `<span class="tok">${esc(k)}<button data-rm="${i}" aria-label="移除">×</button></span>`).join("")}</span>
      <input id="kwin" placeholder="${d.keywords.length ? "继续添加,回车确认" : "输入关键词,回车添加"}"></div>
    <div id="kwsug">${kwSugHtml()}</div>
    ${preset ? `<div class="sugs"><span class="sub">推荐:</span>${preset[1].map(k => `<button class="chip ${d.keywords.includes(k) ? "on" : ""}" data-sug="${esc(k)}">${esc(k)}</button>`).join("")}</div>` : ""}
    <div class="sec-h" style="margin-top:22px"><span class="no">3</span><b>想做的内容</b><span class="sub">会贯穿拆解和复刻,写得越具体越好</span></div>
    <textarea id="dir" rows="3" placeholder="例如:面向学生党的平价通勤妆教程,偏干货,强调 10 分钟内完成">${esc(d.direction)}</textarea>
    <div class="row" style="margin-top:16px;justify-content:flex-end;gap:10px"><span class="sub" id="trhint">选好赛道就能一键生成,关键词可以不填</span>
      <label class="sub">篇数 <input id="acount" type="number" min="1" max="50" value="${state.acount || 10}" style="width:70px;display:inline-block"></label>
      <button class="btn gray lg" id="create">手动流程 →</button><button class="btn lg" id="auto">一键生成 →</button></div>
  </section>
  ${state.projects.length ? `<div class="list-h"><b>已有项目</b><span class="sub">${state.projects.length} 个</span></div>
  <div class="plist">${state.projects.map(p => `<div class="card pi ${p.id === state.pid ? "on" : ""}" data-open="${p.id}">
    <div class="row"><span class="tag-t">${esc(p.track)}</span>${p.crawling ? `<span class="st run">采集中</span>` : ""}${p.auto_status ? `<span class="st ${["done"].includes(p.auto_status) ? "ready" : ["error", "canceled"].includes(p.auto_status) ? "err" : "run"}">批量${AUTO_ST[p.auto_status] || ""}</span>` : ""}<span style="flex:1"></span><button class="btn ghost sm danger" data-delp="${p.id}">删除</button></div>
    <b class="pk">${esc(p.keywords.join("、"))}</b><div class="sub pdir">${esc(p.direction) || "未填写内容方向"}</div>
    <div class="sub">${p.note_count} 篇笔记 · ${p.draft_count} 篇草稿 · ${esc(lt(p.created, "ymd"))} ${esc(lt(p.created).slice(6))}</div></div>`).join("")}</div>` : ""}`;
  const sync = () => { d.direction = $("#dir").value; if ($("#tcustom")) d.track = $("#tcustom").value.trim() || "__other"; };
  const addKw = v => { for (const k of v.split(/[,，、;；]+/).map(x => x.trim()).filter(Boolean)) if (!d.keywords.includes(k) && d.keywords.length < 5) d.keywords.push(k); };
  $("#main").onclick = async e => {
    const mb = e.target.closest("[data-mode]"); if (mb) { sync(); state.mode = mb.dataset.mode; return vTrack(); }
    const sp = e.target.closest("[data-sugpick]");
    if (sp) { sync(); const w = state.kwSug?.word, k = sp.dataset.sugpick; d.keywords = d.keywords.filter(x => x !== w); if (!d.keywords.includes(k) && d.keywords.length < 5) d.keywords.push(k); state.kwSug = null; return vTrack(); }
    if (e.target.closest("[data-sugkeep]")) { state.kwSug = null; $("#kwsug").innerHTML = ""; return; }
    const t = e.target.closest("[data-t]"); if (t) { sync(); d.track = t.dataset.t; return vTrack(); }
    const s = e.target.closest("[data-sug]"); if (s) { sync(); const k = s.dataset.sug; d.keywords.includes(k) ? d.keywords.splice(d.keywords.indexOf(k), 1) : addKw(k); return vTrack(); }
    const rm = e.target.closest("[data-rm]"); if (rm) { sync(); d.keywords.splice(+rm.dataset.rm, 1); return vTrack(); }
    const dp = e.target.closest("[data-delp]"); if (dp) { e.stopPropagation(); if (confirm("删除这个项目及其拆解卡、草稿?(采集到的笔记仍保留在笔记库)")) { await api("/api/projects/" + dp.dataset.delp, "DELETE"); render(); } return; }
    const op = e.target.closest("[data-open]"); if (op) { setProject(+op.dataset.open); return go(project()?.auto_status ? "export" : "crawl"); }
  };
  $("#kwin").onkeydown = async e => {
    if (e.key !== "Enter" || e.isComposing) return;
    sync(); const before = [...d.keywords]; addKw(e.target.value);
    const added = d.keywords.filter(k => !before.includes(k)); state.kwSug = null;
    await vTrack(); $("#kwin").focus();
    const w = added.find(isBroad);  // 填了宽泛的词:问问 AI 有没有更具体的说法
    if (w) {
      const r = await kwSuggest(w, d.track === "__other" ? "" : d.track);
      if (r.broad && r.suggestions.length && d.keywords.includes(w) && $("#kwsug")) { state.kwSug = { word: w, r }; $("#kwsug").innerHTML = sugHtml(w, r, "data-sugpick"); }
    }
  };
  $("#auto").onclick = async () => {
    sync(); if ($("#kwin").value.trim()) addKw($("#kwin").value);
    const track = d.track === "__other" ? "" : d.track;
    if (!track) return toast("请先选择赛道", 1);
    const count = Math.max(1, Math.min(+$("#acount").value || 10, 50)); state.acount = count;
    $("#auto").disabled = true;
    try {
      const r = await api("/api/auto", "POST", { track, keywords: d.keywords, direction: d.direction, count });
      setProject(r.id); state.draft = { track: "", keywords: [], direction: "" };
      toast(`已开始:${count} 篇,关键词「${r.keywords.join("、")}」`); openRun(r.id);
    } catch (e) { toast(e.message, 1); $("#auto").disabled = false; }
  };
  $("#create").onclick = async () => {
    sync(); if ($("#kwin").value.trim()) addKw($("#kwin").value);
    const track = d.track === "__other" ? "" : d.track;
    if (!track) return toast("请先选择赛道", 1);
    if (!d.keywords.length) return toast("请至少添加一个关键词", 1);
    try {
      const { id } = await api("/api/projects", "POST", { track, keywords: d.keywords, direction: d.direction });
      setProject(id); state.draft = { track: "", keywords: [], direction: "" }; toast("已创建,采集任务已下发"); go("crawl");
    } catch (e) { toast(e.message, 1); }
  };
}

// ---------- 2 采集 ----------
const IMG = (u, cls = "cv") => u ? `<img class="${cls}" src="${esc(u)}" referrerpolicy="no-referrer" loading="lazy" onerror="this.outerHTML='<div class=ph-img>封面加载失败</div>'">` : `<div class="ph-img">无封面</div>`;
async function vCrawl() {
  const p = project();
  $("#pa").innerHTML = `<span class="sub">已选 <b id="pc" style="color:var(--accent-ink)">${state.picked.size}</b> 篇</span>
    <button class="btn gray" id="all">选前 5</button><button class="btn gray" id="none">清空</button>
    <button class="btn" id="next">拆解选中笔记 →</button>`;
  $("#main").innerHTML = `<div class="crawl-top">
    <section class="card"><div class="card-h"><b>采集任务</b><span id="coll"></span></div>
      <div class="row addtask"><select id="ty"><option value="keyword">按关键词</option><option value="title">按标题</option><option value="author">按博主</option></select>
        <input id="tq" class="grow" placeholder="追加一个关键词"><button class="btn gray" id="tadd">追加任务</button></div><div id="tsug"></div>
      <div id="tasks" class="tasks"></div></section>
    <section class="card brief"><div class="card-h"><b>本项目</b><button class="btn ghost sm" id="pedit" style="margin-left:auto">编辑</button><button class="btn ghost sm" data-go="track">新建赛道</button></div>
      <div class="kv"><span>赛道</span><b>${esc(p.track)}</b></div>
      <div class="kv"><span>关键词</span><b>${esc(p.keywords.join("、"))}</b></div>
      <div class="kv"><span>内容方向</span><p>${esc(p.direction) || "未填写"}</p></div></section>
  </div>
  <div class="list-h"><b>采集到的笔记</b><span class="sub" id="ncount"></span><span class="sub" style="margin-left:auto">按点赞排序 · 点击卡片选择要拆解的笔记</span></div>
  <div class="nfilter" id="nfilter"></div>
  <div id="notes"></div>`;
  let notes = [], sig = "";
  const nf = state.nfil ||= { tier: "", type: "", topic: "", all: false };
  const hiddenOf = t => t.rel === "low" || t.old || t.dup_of;  // 弱相关(搜索词只是顺带提到)、超出时间范围、和别的笔记重复
  const hiddenInfo = () => { const h = notes.filter(hiddenOf); return { n: h.length, weak: h.filter(t => t.rel === "low").length, old: h.filter(t => t.old).length, dup: h.filter(t => t.dup_of).length }; };
  const showAll = () => nf.all || !notes.some(t => !hiddenOf(t));  // 全都被隐藏时(比如老数据)就不隐藏,免得页面空着
  const shown = () => notes.filter(t => (showAll() || !hiddenOf(t)) && (!nf.tier || t.tier === nf.tier) && (!nf.type || t.ai_type === nf.type) && (!nf.topic || t.topic === nf.topic));
  const drawNotes = () => {
    const list = shown(), types = [...new Set(notes.map(t => t.ai_type).filter(Boolean))], topics = [...new Set(notes.map(t => t.topic).filter(x => x && x !== "未识别"))];
    $("#ncount").textContent = notes.length ? (list.length === notes.length ? `${notes.length} 篇` : `${list.length} / ${notes.length} 篇`) : "";
    const hid = hiddenInfo();
    $("#nfilter").innerHTML = notes.length ? `${hid.n ? `<button class="chip ${nf.all ? "on" : ""}" data-fall title="弱相关:搜索词只是顺带提到;较早:发布时间超出采集范围">${nf.all ? "只看相关的近期笔记" : `显示被隐藏的 ${hid.n} 篇(弱相关 ${hid.weak}、较早 ${hid.old}${hid.dup ? `、重复 ${hid.dup}` : ""})`}</button><span style="width:10px"></span>` : ""}${topics.length ? `<span class="sub">主题</span>${[["", "全部"], ...topics.map(t => [t, t])].map(([k, n]) => `<button class="chip ${nf.topic === k ? "on" : ""}" data-fp="${esc(k)}">${esc(n)}</button>`).join("")}<span style="width:10px"></span>` : ""}<span class="sub">分级</span>${[["", "全部"], ["viral", "爆款"], ["hot", "热门"]].map(([k, n]) => `<button class="chip ${nf.tier === k ? "on" : ""}" data-ft="${k}">${n}</button>`).join("")}
      ${types.length ? `<span class="sub" style="margin-left:10px">类型</span>${[["", "全部"], ...types.map(t => [t, t])].map(([k, n]) => `<button class="chip ${nf.type === k ? "on" : ""}" data-fy="${esc(k)}">${esc(n)}</button>`).join("")}` : ""}` : "";
    $("#notes").innerHTML = list.length ? `<div class="wall">${list.map(t => `<div class="nc ${state.picked.has(t.id) ? "sel" : ""}" data-id="${t.id}">
      ${IMG(t.cover)}<div class="ck">✓</div>
      <div class="badges">${t.dup_of ? `<span class="tier normal" title="和另一篇笔记重复(换号搬运或同一模板反复发),拆解时只用互动量最高的那篇">重复</span>` : ""}${t.rel === "low" ? `<span class="tier normal" title="${esc(t.rel_why)}">弱相关</span>` : ""}${t.old ? `<span class="tier normal" title="发布于 ${esc(t.published_at)}">较早</span>` : ""}${t.note_type === "video" ? `<span class="tp">视频</span>` : ""}${t.tier !== "normal" ? `<span class="tier ${t.tier}">${t.tier === "viral" ? "爆款" : "热门"}</span>` : ""}${t.topic && t.topic !== "未识别" ? `<span class="tp">${esc(t.topic)}</span>` : ""}${t.ai_type ? `<span class="tp">${esc(t.ai_type)}</span>` : ""}</div>
      <div class="ops"><span data-view="${t.id}">详情</span><span data-del="${t.id}">移出</span></div>
      <div class="bd"><div class="tt">${esc(t.title)}</div>${t.ai_highlight ? `<div class="hl">${esc(t.ai_highlight)}</div>` : `<div class="ds">${esc(t.content)}</div>`}
      <div class="ft"><span>${esc(t.author || "-")}</span><span>♥ <b>${fmt(t.likes)}</b>${t.collects ? ` · ★ ${fmt(t.collects)}` : ""}</span></div></div></div>`).join("")}</div>`
      : notes.length ? `<div class="empty"><b>没有符合筛选的笔记</b></div>` : `<div class="empty"><b>还没有笔记</b>采集端领取任务并回传后,笔记会自动出现在这里</div>`;
  };
  const refresh = async () => {
    const [c, ns] = await Promise.all([api(`/api/projects/${p.id}/crawl`), api(`/api/projects/${p.id}/notes`)]);
    $("#coll").innerHTML = c.collector.online ? `<span class="dot on"></span><span class="sub">采集端在线</span>`
      : `<span class="dot"></span><span class="sub">采集端未连接,任务会在采集端上线后自动执行</span>`;
    $("#tasks").innerHTML = c.tasks.map(t => `<div class="task"><span class="ty">${TYPE_NAME[t.type] || "关键词"}</span><b>${esc(t.query)}</b>${st(t.status)}
      <span class="sub">${t.status === "done" || t.note_count ? `${t.note_count} 篇` : `目标 ${t.lim} 篇`}</span>
      ${t.error ? `<span class="terr" title="${esc(t.error)}">${esc(t.error)}</span>` : ""}
      <span class="acts">${t.status === "failed" || t.status === "done" ? `<button class="btn ghost sm" data-retry="${t.id}">${t.status === "done" ? "再采一次" : "重试"}</button>` : ""}</span></div>`).join("") || `<div class="sub" style="padding:8px 0">没有任务</div>`;
    const s = ns.map(n => n.id + n.ai_type + n.tier + n.topic + n.rel + n.old).join(",");
    if (s !== sig) { sig = s; notes = ns; drawNotes(); }
    const crawling = c.tasks.filter(t => BUSY.includes(t.status)).length;
    if (crawling !== p.crawling) { p.crawling = crawling; nav(); }
    return true;
  };
  await refresh(); loop(refresh, 3000);
  $("#pedit").onclick = () => {
    const m = document.createElement("div"); m.className = "mask";
    m.innerHTML = `<form class="card" style="width:100%;max-width:520px;padding:24px"><div class="card-h"><b>编辑项目</b></div>
      <label class="f" style="margin-top:0">关键词<span class="sub"> · 用逗号分隔,每个词是独立的搜索词,最多 5 个</span></label><input id="ek" value="${esc(p.keywords.join("，"))}">
      <label class="f">内容方向<span class="sub"> · 告诉 AI 这批内容的主题和要求,写得越具体越好</span></label>
      <textarea id="ed" rows="4" placeholder="例如:只写姜黄粉的吃法和搭配;咖啡、腐乳是分开的主题,不要混在一起">${esc(p.direction || "")}</textarea>
      <p class="sub" style="margin:8px 0 0">修改只影响之后的拆解和复刻,不会重新采集。</p>
      <div class="row" style="margin-top:16px;justify-content:flex-end"><button type="button" class="btn gray" data-x>取消</button><button class="btn">保存</button></div></form>`;
    m.onclick = ev => { if (ev.target === m || ev.target.closest("[data-x]")) m.remove(); };
    m.querySelector("form").onsubmit = async ev => {
      ev.preventDefault();
      try { await api(`/api/projects/${p.id}`, "PUT", { keywords: [$("#ek").value], direction: $("#ed").value }); m.remove(); toast("已保存"); await loadProjects(); render(); } catch (err) { toast(err.message, 1); }
    };
    document.body.appendChild(m);
  };
  $("#ty").onchange = e => { $("#tq").placeholder = TYPE_HINT[e.target.value]; };
  $("#tq").onkeydown = e => { if (e.key === "Enter" && !e.isComposing) $("#tadd").click(); };
  const addTask = async (type, query) => {
    try { await api(`/api/projects/${p.id}/crawl`, "POST", { type, query }); $("#tq").value = ""; $("#tsug").innerHTML = ""; refresh(); } catch (e) { toast(e.message, 1); }
  };
  $("#tadd").onclick = async () => {
    const q = $("#tq").value.trim(), type = $("#ty").value;
    if (!q) return toast("请输入关键词或标题", 1);
    if (type === "keyword" && isBroad(q)) {  // 宽泛的词先给出更具体的选项,用户可以选一个,也可以坚持用原词
      $("#tsug").innerHTML = `<div class="sub" style="padding:8px 0">正在看看「${esc(q)}」是不是太宽泛…</div>`;
      const r = await kwSuggest(q, p.track);
      if (r.broad && r.suggestions.length) {
        $("#tsug").innerHTML = sugHtml(q, r, "data-addtask");
        $("#tsug").onclick = ev => { const b = ev.target.closest("[data-addtask]"); if (b) return addTask("keyword", b.dataset.addtask); if (ev.target.closest("[data-sugkeep]")) addTask("keyword", q); };
        return;
      }
    }
    addTask(type, q);
  };
  $("#main").onclick = async e => {
    const t = e.target, card = t.closest(".nc");
    if (t.dataset.retry) { await api(`/api/crawl_tasks/${t.dataset.retry}/retry`, "POST"); return refresh(); }
    if (t.dataset.fall !== undefined) { nf.all = !nf.all; return drawNotes(); }
    if (t.dataset.fp !== undefined) { nf.topic = t.dataset.fp; return drawNotes(); }
    if (t.dataset.ft !== undefined) { nf.tier = t.dataset.ft; return drawNotes(); }
    if (t.dataset.fy !== undefined) { nf.type = t.dataset.fy; return drawNotes(); }
    if (t.dataset.del) { if (confirm("把这篇笔记移出本项目?(笔记库里仍保留)")) { state.picked.delete(+t.dataset.del); await api(`/api/projects/${p.id}/notes/${t.dataset.del}`, "DELETE"); refresh(); } return; }
    if (t.dataset.view) return openNote(notes.find(n => n.id === +t.dataset.view));
    if (card) {
      const id = +card.dataset.id; state.picked.has(id) ? state.picked.delete(id) : state.picked.add(id);
      card.classList.toggle("sel"); $("#pc").textContent = state.picked.size; nav();
    }
  };
  $("#all").onclick = () => { shown().slice(0, 5).forEach(t => state.picked.add(t.id)); drawNotes(); $("#pc").textContent = state.picked.size; nav(); };
  $("#none").onclick = () => { state.picked.clear(); drawNotes(); $("#pc").textContent = 0; nav(); };
  $("#next").onclick = async () => {
    const ids = [...state.picked].filter(id => notes.some(n => n.id === id));
    if (!ids.length) return toast("请先点选要拆解的笔记,建议 3-8 篇", 1);
    const tps = [...new Set(notes.filter(n => ids.includes(n.id)).map(n => n.topic).filter(x => x && x !== "未识别"))];
    if (tps.length > 1 && !confirm(`选中的笔记涉及 ${tps.length} 个不同主题:${tps.join("、")}。\n建议每次只拆解同一个主题,否则「可写主题」会混在一起。\n仍然一起拆解?`)) return;
    try { const { id } = await api(`/api/projects/${p.id}/playbooks`, "POST", { note_ids: ids }); state.pbSel = id; go("analyze"); } catch (e) { toast(e.message, 1); }
  };
}
function openNote(t) {
  const pics = t.images && t.images.length ? t.images : (t.cover ? [t.cover] : []);
  const m = document.createElement("div"); m.className = "mask";
  m.innerHTML = `<div class="modal">${pics.length ? `<div class="l">${pics.map(u => `<img src="${esc(u)}" referrerpolicy="no-referrer">`).join("")}</div>` : ""}
    <button class="btn ghost sm x">✕</button>
    <div class="r"><h3 style="margin:0 24px 4px 0;font-size:18px;line-height:1.45">${esc(t.title)}</h3>
    <div class="sub">${esc(t.author || "-")} · ♥ ${fmt(t.likes)} · ★ ${fmt(t.collects)} · 💬 ${fmt(t.comments)}${t.published_at ? " · " + esc(t.published_at) : ""}</div>
    ${t.ai_type ? `<div class="angle" style="margin-top:12px"><span>${esc(t.ai_type)}</span>${esc(t.ai_audience)}${t.ai_highlight ? " · " + esc(t.ai_highlight) : ""}</div>` : ""}
    <p style="white-space:pre-wrap;line-height:1.8">${esc(t.content) || "(没有正文)"}</p>
    ${t.tags?.length ? `<p class="sub">${t.tags.map(x => "#" + esc(x)).join(" ")}</p>` : ""}
    ${t.url && t.url.startsWith("http") ? `<a class="btn gray sm" href="${esc(t.url)}" target="_blank" rel="noreferrer" style="text-decoration:none">在小红书打开 ↗</a>` : ""}</div></div>`;
  const close = () => { m.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = e => { if (e.key === "Escape") close(); };
  m.onclick = e => { if (e.target === m || e.target.closest(".x")) close(); };
  document.addEventListener("keydown", onKey);
  document.body.appendChild(m);
}

// ---------- 3 拆解 ----------
const PB_FIELDS = [["summary", "爆点总结", 0], ["topics", "可写主题(每篇只写其中一个,不想写的删掉)", 1], ["benchmarks", "量化规律", 1], ["title_formulas", "标题公式", 1], ["hooks", "开头钩子", 1], ["structure", "正文结构", 0], ["tone", "语气人设", 0],
["format", "字数与排版", 0], ["tag_strategy", "话题策略", 0], ["cover_style", "封面风格", 0], ["image_style", "配图风格", 0], ["dos", "必须做到", 1], ["donts", "避免踩雷", 1]];
const EVI = { "数据": "data", "归纳": "infer", "知识库": "kb" };
const eviBadge = v => v ? String(v).split(/[\/、,，]/).map(x => x.trim()).filter(x => EVI[x]).map(x => `<span class="evi ${EVI[x]}">${x}</span>`).join("") : "";
const STAT_ROWS = [["title_len", "标题字数", v => v.toFixed(1)], ["t_digit", "标题含数字", v => Math.round(v * 100) + "%"], ["t_mark", "标题含！/？", v => Math.round(v * 100) + "%"],
["body_len", "正文字数(中位数)", v => Math.round(v)], ["paras", "段落数", v => v.toFixed(1)], ["emoji_100", "每百字 emoji", v => v.toFixed(1)],
["list_ratio", "分点段落占比", v => Math.round(v * 100) + "%"], ["cta", "结尾有互动引导", v => Math.round(v * 100) + "%"], ["tags", "话题数", v => v.toFixed(1)], ["images", "配图数", v => v.toFixed(1)]];
function statsTable(st) {
  if (!st || !st.ref) return "";
  const lb = st.scope?.startsWith("topic:") ? st.scope.slice(6) : "赛道";
  const cols = [["本次参考", st.ref], [`${lb}爆款/热门`, st.top], [`${lb}普通`, st.normal]].filter(c => c[1]);
  return `<details class="pbstats" open><summary><b>样本统计</b><span class="sub">拆解卡里标「数据」的结论来自这张表${st.comparable ? "" : ` · ${lb}爆款或普通样本不足 5 篇,暂不做对比结论`}</span></summary>
    ${(st.warnings || []).map(w => `<div class="banner warn" style="margin:8px 12px">${esc(w)}</div>`).join("")}
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th>指标</th>${cols.map(([n, g]) => `<th class="num">${n}<div class="sub">n=${g.n}</div></th>`).join("")}</tr></thead>
    <tbody>${STAT_ROWS.map(([k, label, f]) => `<tr><td>${label}</td>${cols.map(([, g]) => `<td class="num">${f(g[k] ?? 0)}</td>`).join("")}</tr>`).join("")}</tbody></table></div></details>`;
}
const asText = v => Array.isArray(v) ? v.map(x => typeof x === "string" ? x : JSON.stringify(x, null, 0)).join("\n") : v && typeof v === "object" ? Object.entries(v).map(([k, x]) => `${k}:${typeof x === "string" ? x : JSON.stringify(x)}`).join("\n") : (v ?? "");
async function vAnalyze() {
  const p = project();
  const [pbs, notes] = await Promise.all([api(`/api/projects/${p.id}/playbooks`), api(`/api/projects/${p.id}/notes`)]);
  if (!pbs.length) {
    $("#main").innerHTML = `<div class="empty"><b>还没有拆解卡</b>先在「采集」里点选几篇爆款笔记,再点「拆解选中笔记」<br><button class="btn" data-go="crawl">去选笔记</button></div>`; return;
  }
  if (!pbs.some(b => b.id === state.pbSel)) state.pbSel = pbs[0].id;
  const pb = pbs.find(b => b.id === state.pbSel), byId = Object.fromEntries(notes.map(n => [n.id, n]));
  $("#pa").innerHTML = pb.status === "done" ? `<button class="btn" id="use">用这张卡去复刻 →</button>` : "";
  $("#main").innerHTML = `<div class="pb-layout">
    <aside class="pb-list">${pbs.map(b => `<button class="${b.id === pb.id ? "on" : ""}" data-pb="${b.id}"><b>${esc(b.name)}</b>
      <span class="row" style="gap:6px">${st(b.status)}<span class="sub">${b.note_ids.length} 篇参考</span></span></button>`).join("")}
      <button class="pb-new" data-go="crawl">＋ 再选笔记拆解</button></aside>
    <section class="card pb-detail">
      <div class="dc-h"><input id="pbname" class="title-in" value="${esc(pb.name)}" style="max-width:360px">${st(pb.status)}
        <div class="acts"><button class="btn ghost sm" id="pbre">重新拆解</button><button class="btn ghost sm danger" id="pbdel">删除</button></div></div>
      <div class="refs"><span class="sub">参考笔记</span>${pb.note_ids.map(id => byId[id] ? `<button class="chip" data-view="${id}">${esc(byId[id].title)} · ♥${fmt(byId[id].likes)}</button>` : "").join("")}</div>
      ${pb.status === "error" ? `<div class="banner err">⚠ ${esc(pb.error)}</div>` : ""}
      ${pb.status === "done" ? `${statsTable(pb.stats)}<div class="pb-grid">${PB_FIELDS.map(([k, label, list]) => `<div class="${["summary", "structure", "benchmarks", "topics"].includes(k) ? "wide" : ""}">
          <label class="f">${label}${eviBadge(pb.data.evidence?.[k])}${list ? '<span class="sub"> · 每行一条</span>' : ""}</label>
          <textarea data-pf="${k}" data-list="${list}" rows="${k === "structure" ? 6 : k === "summary" ? 2 : 4}">${esc(asText(pb.data[k]))}</textarea></div>`).join("")}</div>
        <div class="dc-f"><span class="sub">可以直接修改,复刻时按修改后的内容执行</span><button class="btn" id="pbsave">保存修改</button></div>`
      : pb.status !== "error" ? `<div class="empty" style="border:0"><b>正在拆解…</b>DeepSeek 正在分析 ${pb.note_ids.length} 篇笔记,通常需要 20-60 秒</div>` : ""}
    </section></div>`;
  $("#main").onclick = async e => {
    const b = e.target.closest("[data-pb]"); if (b) { state.pbSel = +b.dataset.pb; return vAnalyze(); }
    const v = e.target.closest("[data-view]"); if (v) return openNote(byId[v.dataset.view]);
  };
  $("#pbre").onclick = async () => { await api(`/api/playbooks/${pb.id}/retry`, "POST"); vAnalyze(); };
  $("#pbdel").onclick = async () => { if (confirm("删除这张拆解卡?")) { await api("/api/playbooks/" + pb.id, "DELETE"); state.pbSel = null; vAnalyze(); } };
  const save = async () => {
    const data = { ...pb.data }; $$("[data-pf]").forEach(t => data[t.dataset.pf] = t.dataset.list === "1" ? t.value.split("\n").map(s => s.trim()).filter(Boolean) : t.value);
    await api("/api/playbooks/" + pb.id, "PUT", { name: $("#pbname").value.trim() || pb.name, data });
  };
  if ($("#pbsave")) $("#pbsave").onclick = async () => { try { await save(); toast("已保存"); } catch (e) { toast(e.message, 1); } };
  if ($("#use")) $("#use").onclick = async () => { try { await save(); } catch { } go("generate"); };
  if (pbs.some(b => ["queued", "running"].includes(b.status))) loop(async () => { const n = await api(`/api/projects/${p.id}/playbooks`); if (n.some(b => b.status !== pbs.find(o => o.id === b.id)?.status)) { vAnalyze(); return false; } return true; }, 2500);
}

// ---------- 4 复刻 ----------
async function vGenerate() {
  const p = project(), pbs = (await api(`/api/projects/${p.id}/playbooks`)).filter(b => b.status === "done");
  if (!pbs.some(b => b.id === state.pbSel)) state.pbSel = pbs[0]?.id || null;
  $("#main").innerHTML = `<section class="card ctl">
    <div class="fld"><label>拆解卡</label><select id="pbs">${pbs.map(b => `<option value="${b.id}" ${b.id === state.pbSel ? "selected" : ""}>${esc(b.name)}</option>`).join("") || `<option value="">暂无可用拆解卡</option>`}</select></div>
    <div class="fld"><label>篇数</label><div class="stepper"><button data-s="-1" aria-label="减少">−</button><input id="cnt" type="number" min="1" max="50" value="5"><button data-s="1" aria-label="增加">+</button></div></div>
    <div class="fld"><label>额外要求(可选)</label><input id="extra" placeholder="如:语气更幽默、加入价格信息"></div>
    <label class="switch"><input type="checkbox" id="wimg" checked><i></i>自动配图</label>
    <button class="btn lg" id="run">开始复刻</button>
    <div class="ctl-meta"><span>内容方向 <b>${esc(p.direction) || "未填写"}</b></span>
      ${pbs.length ? "" : `<a data-go="analyze">还没有完成的拆解卡,去拆解</a>`}
      <div class="prog" id="progwrap" hidden><div class="pbar"><i id="pbar"></i></div><span id="prog"></span></div></div>
  </section>
  <div class="list-h"><b>草稿</b><span class="sub" id="dcount"></span><span class="sub" style="margin-left:auto">任务在后台运行,关掉页面也不会中断</span></div>
  <div class="drafts" id="drafts"></div>`;
  $$(".stepper button").forEach(b => b.onclick = () => { const c = $("#cnt"); c.value = Math.min(50, Math.max(1, (+c.value || 1) + +b.dataset.s)); });
  $("#pbs").onchange = e => state.pbSel = +e.target.value;
  $("#run").onclick = async () => {
    if (!$("#pbs").value) return toast("请先完成一张拆解卡", 1);
    $("#run").disabled = true;
    try { await api(`/api/projects/${p.id}/generate`, "POST", { playbook_id: +$("#pbs").value, count: +$("#cnt").value || 1, extra: $("#extra").value, with_images: $("#wimg").checked }); toast("已加入队列"); await showDrafts(true); }
    catch (e) { toast(e.message, 1); }
    $("#run").disabled = false;
  };
  showDrafts(true);
}
const dsig = d => [d.status, d.variant?.showing, d.title, d.images.filter(Boolean).length, d.error, d.angle, d.checks?.high, d.checks?.low, d.checks?.placeholders?.length, d.checks?.verify?.length, d.checks?.combo?.length, d.review?.overall, d.review?.applied, d.review?.error].join("|");
const FNAME = { title: "标题", content: "正文", tags: "话题", image_text: "配图文字" };
const SEV = { high: "严重", medium: "一般", low: "轻微" };
const gradeB = (g, n) => `<span class="grade g-${g}">${g}</span>${n != null ? `<b class="gnum">${n}</b>` : ""}`;
function reviewPanel(d) {
  const r = d.review || {};
  if (d.status === "reviewing") return `<div class="rv"><div class="rv-h"><span class="st run">审稿中</span><span class="sub">对照拆解卡打分、找问题、出修改版…</span></div></div>`;
  if (r.error) return `<div class="rv"><div class="rv-h"><span class="sub">审稿失败:${esc(r.error)}</span><button class="btn gray sm" data-a="review" style="margin-left:auto">重新审稿</button></div></div>`;
  if (r.grade == null) return d.title && !BUSY.includes(d.status) ? `<div class="rv"><div class="rv-h"><span class="sub">还没有审稿</span><button class="btn gray sm" data-a="review" style="margin-left:auto">审稿打分</button></div></div>` : "";
  const q = r.quant || {}, rv = r.revised;
  const bar = (label, v) => `<div class="rv-bar"><span>${label}</span><i><b style="width:${v}%"></b></i><em>${v}</em></div>`;
  return `<details class="rv"><summary class="rv-h">${gradeB(r.grade, r.overall)}<span class="rv-sum">${esc(r.summary)}</span>
      ${r.applied ? `<span class="tier normal">已采用修改版</span>` : ""}<span class="sub rv-more">展开审稿</span></summary>
    <div class="rv-body">
      <div class="rv-dims">${Object.entries(r.dims || {}).map(([k, v]) => bar(k, v)).join("")}${q.score != null ? bar("量化贴合", q.score) : ""}
        ${r.penalty ? `<div class="sub" style="grid-column:1/-1">合规扣分 −${r.penalty}(高风险词 8 分/个)</div>` : ""}</div>
      ${q.items?.length ? `<div class="rv-q">${q.bench ? `<span class="sub" style="flex-basis:100%">对照基准:${esc(q.bench)}</span>` : ""}${q.items.map(i => `<span class="${i.ok ? "ok" : "no"}">${i.ok ? "✓" : "✗"} ${esc(i.label)} ${esc(i.value)}<small>参考 ${esc(i.target)}</small></span>`).join("")}</div>` : ""}
      ${(r.issues || []).map(i => `<div class="rv-i ${esc(i.severity)}"><div><span class="sev">${SEV[i.severity] || ""}</span><b>${esc(i.dimension)}</b> ${esc(i.problem)}</div>
        ${i.fix ? `<div class="fix">${esc(i.fix)}</div>` : ""}${i.why ? `<div class="sub">${esc(i.why)}</div>` : ""}</div>`).join("")}
      ${rv ? `<div class="rv-rev"><div class="rv-h"><b>修改版</b>
          ${rv.checks?.high ? `<span class="tier viral">高风险 ${rv.checks.high}</span>` : ""}${rv.lost_placeholders?.length ? `<span class="tier hot">丢了 ${rv.lost_placeholders.length} 个占位符</span>` : ""}
          ${r.applied ? "" : `<button class="btn sm" data-a="apply" style="margin-left:auto">采用修改版</button>`}</div>
        <div class="rv-t">${esc(rv.title)}</div><pre>${esc(rv.content)}</pre><div class="sub">${rv.tags.map(t => "#" + esc(t)).join(" ")}</div></div>` : ""}
      ${r.comments?.length ? `<div class="rv-c"><div class="rv-h"><b>模拟评论区</b><span class="sub">AI 模拟,用来预判读者会问什么、质疑什么</span></div>
        ${r.comments.map(c => `<div class="cm"><span class="ct ${c.type === "质疑" ? "q" : c.type === "提问" ? "a" : ""}">${esc(c.type || "")}</span><b>${esc(c.nickname || "")}</b> ${esc(c.text || "")}</div>`).join("")}</div>` : ""}
      <div class="row" style="justify-content:flex-end"><button class="btn ghost sm" data-a="review">重新审稿</button></div>
    </div></details>`;
}
function variantBar(d) {
  const v = d.variant || {}; if (!v.other) return "";
  return `<div class="banner" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">${v.showing === "short"
    ? `<span>已自动压缩:${v.long_len} → ${v.short_len} 字(${esc(v.basis)}正文约 ${v.baseline} 字,超过 ${v.cap} 字才压缩)${v.still_long ? ",压缩后仍偏长" : ""}</span><button class="btn gray sm" data-a="swaplen" style="margin-left:auto">换回原稿(${v.long_len} 字)</button>`
    : `<span>当前是原稿(${v.long_len} 字),压缩版 ${v.short_len} 字</span><button class="btn gray sm" data-a="swaplen" style="margin-left:auto">用压缩版</button>`}</div>`;
}
function checksPanel(d) {
  const c = d.checks || {}, hits = c.hits || [], ver = c.verify || [], combo = c.combo || [];
  const holes = Object.values((c.placeholders || []).reduce((m, x) => { (m[x.text] ||= { ...x, n: 0 }).n++; return m; }, {}));
  if (!d.title || !c.hits) return "";
  if (!hits.length && !holes.length  && !combo.length) return `<div class="chk ok">✓ 合规检查通过${c.fixed ? ` · 已自动修正 ${c.fixed} 处` : ""}${(c.confirmed || []).length ? ` · 经历描述已自动确认 ${c.confirmed.length} 处` : ""}</div>`;
  return `<div class="chk ${c.high || combo.length ? "bad" : holes.length ? "todo" : "warn"}">
    <div class="chk-h"><b>合规检查</b>${c.high ? `<span class="tier viral">高风险 ${c.high}</span>` : ""}${combo.length ? `<span class="tier viral">选题冲突 ${combo.length}</span>` : ""}${c.low ? `<span class="tier hot">建议改 ${c.low}</span>` : ""}${holes.length ? `<span class="tier normal">待补充 ${(c.placeholders || []).length}</span>` : ""}
      ${c.fixed ? `<span class="sub">已自动修正 ${c.fixed} 处</span>` : ""}${c.fix_error ? `<span class="sub">自动修正失败</span>` : ""}
      ${hits.length ? `<button class="btn gray sm" data-a="fix" style="margin-left:auto">按建议修改</button>` : ""}</div>
    ${hits.map(h => `<div class="chk-i ${h.severity}"><span>${esc(h.category)}</span><b>「${esc(h.word)}」</b><span class="sub">${FNAME[h.field] || ""}${h.count > 1 ? ` ×${h.count}` : ""} → ${esc(h.replacement || "换个说法")}</span></div>`).join("")}
    ${combo.map(p => `<div class="chk-i high"><span>选题冲突</span><b>「${esc(p[0])}」+「${esc(p[1])}」</b><span class="sub">写在了同一篇,参考笔记里没有这种搭配,请删掉其中一个</span></div>`).join("")}
    ${holes.map(x => `<div class="chk-i hole"><span>待补充</span><b>【补充:${esc(x.text)}】${x.n > 1 ? ` ×${x.n}` : ""}</b><span class="sub">${FNAME[x.field] || ""} · 换成你的真实信息后保存</span></div>`).join("")}
  </div>`;
}
function draftCard(d) {
  const busy = BUSY.includes(d.status);
  const okImgs = d.images.filter(Boolean).length, missing = Math.max(0, d.image_prompts.length - okImgs);
  return `<article class="card dc ${busy ? "busy" : ""}" data-id="${d.id}" data-sig="${esc(dsig(d))}">
    <div class="dc-media">${okImgs ? `${d.images[0] ? `<img class="main-img" src="/images/${esc(d.images[0])}">` : `<div class="ph-img miss">封面未生成</div>`}${d.images.length > 1 ? `<div class="thumbs">${d.images.slice(1).map((i, k) => i ? `<img src="/images/${esc(i)}">` : `<div class="ph-img miss">第${k + 2}张<br>未生成</div>`).join("")}</div>` : ""}` : `<div class="ph-img">${d.status === "drawing" ? "生图中…" : "暂无配图"}</div>`}
      <span class="sub">${okImgs} / ${d.image_prompts.length || "-"} 张图</span></div>
    <div class="dc-body">
      <div class="dc-h">${st(d.status)}<span class="sub">#${d.id}</span>
        <div class="acts"><button class="btn ghost sm" data-a="img" ${busy || !d.image_prompts.length ? "disabled" : ""}>${!okImgs ? "生图" : missing ? `补全缺失的图(${missing})` : "重新生图"}</button>${okImgs && missing ? `<button class="btn ghost sm" data-a="imgall" ${busy ? "disabled" : ""}>全部重画</button>` : ""}<button class="btn ghost sm" data-a="redo" ${busy ? "disabled" : ""}>重写</button><button class="btn ghost sm danger" data-a="del">删除</button></div></div>
      ${d.angle ? `<div class="angle"><span>角度</span>${esc(d.angle)}</div>` : ""}
      ${d.cover_card?.layout ? `<div class="angle"><span>封面</span>${esc(d.image_prompts[0] || "")}<button class="btn ghost sm" data-a="cover" ${busy ? "disabled" : ""}>换个样式</button></div>` : ""}
      ${d.error ? `<div class="banner err">⚠ ${esc(d.error)}</div>` : ""}
      ${variantBar(d)}
      ${checksPanel(d)}
      ${reviewPanel(d)}
      ${d.title || !busy ? `<input class="title-in" data-f="title" value="${esc(d.title)}" placeholder="标题">
      ${d.alt_titles.length ? `<div class="alts"><span class="sub">备选标题</span>${d.alt_titles.map(a => `<button class="chip" data-alt="${esc(a)}" title="点击替换标题">${esc(a)}</button>`).join("")}</div>` : ""}
      <label class="f">正文</label><textarea data-f="content" rows="8">${esc(d.content)}</textarea>
      <div class="two">
        <div><label class="f">话题(逗号分隔)</label><input data-f="tags" value="${esc(d.tags.join(","))}"></div>
        <div><label class="f">生图提示词(每行一张,第 1 张是封面)</label><textarea data-f="image_prompts" rows="3">${esc(d.image_prompts.join("\n"))}</textarea></div>
      </div>
      <div class="dc-f"><span class="sub">修改后记得保存</span><button class="btn" data-a="save">保存修改</button></div>`
      : `<div class="skel"><i></i><i></i><i></i></div>`}
    </div></article>`;
}
async function showDrafts(full) {
  const el = $("#drafts"); if (!el) return;
  const ds = await api(`/api/projects/${project().id}/drafts`);
  const busyN = ds.filter(d => BUSY.includes(d.status)).length, readyN = ds.filter(d => d.status === "ready" || d.status === "text_done").length;
  $("#dcount").textContent = ds.length ? `${ds.length} 篇 · 可导出 ${readyN}` : "";
  if (busyN) { $("#progwrap").hidden = false; $("#prog").textContent = `进行中 ${busyN} 篇`; $("#pbar").style.width = (ds.length - busyN) / ds.length * 100 + "%"; }
  else if (!$("#progwrap").hidden) { const errN = ds.filter(d => d.status === "error").length; $("#prog").textContent = errN ? `已结束 · ${errN} 篇出错` : "全部完成"; $("#pbar").style.width = "100%"; }
  const shown = $$(".dc", el).map(c => c.dataset.id).join(",");
  if (full || shown !== ds.map(d => d.id).join(",")) {
    el.innerHTML = ds.map(draftCard).join("") || `<div class="empty"><b>还没有草稿</b>选好拆解卡和篇数后点「开始复刻」</div>`;
  } else {
    // 只替换状态变了且没在编辑的卡片,避免冲掉正在输入的内容
    ds.forEach(d => { const c = el.querySelector(`.dc[data-id="${d.id}"]`); if (c && c.dataset.sig !== dsig(d) && !c.contains(document.activeElement)) c.outerHTML = draftCard(d); });
  }
  el.onclick = async e => {
    const chip = e.target.closest("[data-alt]");
    if (chip) { chip.closest("[data-id]").querySelector("[data-f=title]").value = chip.dataset.alt; return; }
    const a = e.target.dataset.a; if (!a) return;
    const card = e.target.closest("[data-id]"), id = card.dataset.id, g = f => card.querySelector(`[data-f=${f}]`).value;
    if (a === "del" && !confirm(`删除草稿 #${id}?`)) return;
    if (a === "imgall" && !confirm("全部重画会重新生成所有图片并重新计费。继续?")) return;
    if (a === "swaplen" && !confirm("切换版本会替换当前正文,没保存的手动修改会丢失。继续?")) return;
    if (a === "apply" && !confirm("用修改版替换当前的标题、正文和话题?(没保存的手动修改会丢失)")) return;
    e.target.disabled = true;
    try {
      if (a === "del") await api("/api/drafts/" + id, "DELETE");
      if (a === "fix") await api(`/api/drafts/${id}/fix`, "POST");
      if (a === "cover") { await api(`/api/drafts/${id}/cover`, "POST"); toast("正在换封面样式"); }
      if (a === "swaplen") { await api(`/api/drafts/${id}/swap_length`, "POST"); toast("已切换版本"); }
      if (a === "review") await api(`/api/drafts/${id}/review`, "POST");
      if (a === "apply") { await api(`/api/drafts/${id}/apply_review`, "POST"); toast("已采用修改版"); }
      if (a === "save") { document.activeElement?.blur(); await api("/api/drafts/" + id, "PUT", { title: g("title"), content: g("content"), tags: g("tags").split(/[,,]/).map(s => s.trim()).filter(Boolean), image_prompts: g("image_prompts").split("\n").map(s => s.trim()).filter(Boolean) }); toast("已保存"); }
      if (a === "redo") await api(`/api/drafts/${id}/text`, "POST");
      if (a === "img") await api(`/api/drafts/${id}/images`, "POST");
      if (a === "imgall") await api(`/api/drafts/${id}/images?all=1`, "POST");
    } catch (err) { toast(err.message, 1); }
    await showDrafts(a === "del");
    if (!["fix", "review", "img", "imgall"].includes(a) || !$$(".dc.busy").length) return;
    loop(async () => { await showDrafts("poll"); return $$(".dc.busy").length > 0; }, 2000);
  };
  if (busyN && full !== "poll") loop(async () => { await showDrafts("poll"); return $$(".dc.busy").length > 0; }, 2500);
}

// ---------- 5 导出 ----------
function autoBar(a) {
  if (!a) return "";
  const bad = a.status === "error" || a.status === "canceled", dn = a.drafts;
  return `<section class="card auto-bar ${bad ? "bad" : a.status === "done" ? "ok" : ""}">
    <div class="row"><b>批量生成 · 目标 ${a.target} 篇</b><span style="flex:1"></span>${a.active ? `<button class="btn ghost sm danger" id="acancel">取消</button>` : ""}</div>
    <div class="steps">${AUTO_STEPS.slice(a.product ? 0 : 1).map(([k, n], i) => { const cur = a.step - (a.product ? 0 : 1); return `<span class="stp ${i < cur || a.status === "done" ? "dn" : i === cur ? "on" : ""}">${n}</span>`; }).join("")}</div>
    <div class="${bad ? "err-t" : "sub"}" style="margin-top:8px">${esc(a.msg)}</div>
    <div class="sub" style="margin-top:4px">采集任务 ${a.tasks[0]}/${a.tasks[1]} · 笔记 ${a.notes} 篇 · 拆解卡 ${a.playbooks[0]}/${a.playbooks[1]} · 稿子 ${dn.total} 篇(合格 ${dn.passed}${dn.failed ? `,需检查 ${dn.failed}` : ""})</div>
  </section>`;
}
function openDraft(id) {  // 导出页:点标题查看完整文案、配图、检查结果,可一键复制
  const d = (state.exportDs || []).find(x => x.id === id); if (!d) return;
  const c = d.checks || {}, r = d.review || {}, hits = c.hits || [], holes = c.placeholders || [];
  const imgs = d.images.filter(Boolean).map(i => "/images/" + i);
  const m = document.createElement("div"); m.className = "mask";
  const tags = d.tags.map(t => "#" + t).join(" ");
  m.innerHTML = `<div class="card dv">
    <div class="card-h"><b>#${d.id}${d.auto_ok === 1 ? " · ✓ 合格" : d.auto_ok === 0 ? " · ⚠ 需检查" : ""}</b>${r.grade ? gradeB(r.grade, r.overall) : ""}<span style="flex:1"></span><button class="btn ghost sm" data-x>关闭</button></div>
    ${d.auto_ok === 0 && d.auto_note ? `<div class="banner warn">${esc(d.auto_note)}</div>` : ""}
    ${imgs.length ? `<div class="dv-imgs">${imgs.map((u, i) => `<img src="${esc(u)}" data-i="${i}">`).join("")}</div>` : ""}
    <div class="dv-h"><label class="f">标题 · ${d.title.length} 字</label><button class="btn gray sm" data-copy="title">复制</button></div>
    <div class="dv-t">${esc(d.title)}</div>
    ${d.alt_titles.length ? `<div class="sub">备选:${d.alt_titles.map(esc).join(" / ")}</div>` : ""}
    <div class="dv-h"><label class="f">正文 · ${d.content.length} 字</label><button class="btn gray sm" data-copy="content">复制</button></div>
    <div class="dv-body">${esc(d.content)}</div>
    <div class="dv-h"><label class="f">话题</label><button class="btn gray sm" data-copy="tags">复制</button></div>
    <div class="dv-tags">${esc(tags) || "无"}</div>
    ${hits.length || holes.length || r.summary ? `<div class="dv-h"><label class="f">检查</label></div>
      ${r.summary ? `<div class="sub">审稿:${esc(r.summary)}</div>` : ""}
      ${hits.map(h => `<div class="chk-i ${h.severity}"><span>${esc(h.category)}</span><b>「${esc(h.word)}」</b><span class="sub">${FNAME[h.field] || ""} → ${esc(h.replacement || "换个说法")}</span></div>`).join("")}
      ${holes.map(x => `<div class="chk-i hole"><span>待补充</span><b>【补充:${esc(x.text)}】</b></div>`).join("")}` : ""}
    <div class="row" style="margin-top:18px;justify-content:flex-end;gap:10px"><button class="btn gray" data-copy="all">复制全文(标题+正文+话题)</button><button class="btn" data-edit>去复刻页编辑</button></div>
  </div>`;
  const text = { title: d.title, content: d.content, tags, all: `${d.title}\n\n${d.content}\n\n${tags}` };
  m.onclick = async e => {
    if (e.target === m || e.target.closest("[data-x]")) return m.remove();
    const im = e.target.closest(".dv-imgs img"); if (im) return lightbox(imgs, +im.dataset.i);
    if (e.target.closest("[data-edit]")) { m.remove(); return go("generate"); }
    const cp = e.target.closest("[data-copy]"); if (!cp) return;
    try { await copyText(text[cp.dataset.copy]); toast("已复制"); } catch { toast("复制失败,请手动选中复制", 1); }
  };
  document.body.appendChild(m);
}
async function vExport() {
  const a = await api(`/api/projects/${project().id}/auto`).catch(() => null);
  const ds = (await api(`/api/projects/${project().id}/drafts`)).filter(d => d.title && !BUSY.includes(d.status));
  state.exportDs = ds;
  if (a && a.status === "done" && !state.autoSel?.[state.pid]) {  // 批量生成完成后,默认只勾选合格的
    (state.autoSel ||= {})[state.pid] = 1; state.ready = new Set(ds.filter(d => d.auto_ok === 1).map(d => d.id));
  }
  if (a?.active) { const sig = JSON.stringify(a); loop(async () => { const b = await api(`/api/projects/${state.pid}/auto`).catch(() => null); if (JSON.stringify(b) !== sig) { vExport(); return false; } return true; }, 4000); }
  $("#pa").innerHTML = `<button class="btn gray" id="sel">${a ? "只选合格的" : "全选已就绪"}</button><button class="btn" id="pack">打包导出 ZIP</button>`;
  $("#main").innerHTML = `${autoBar(a)}<div id="res"></div>
    ${ds.length ? `<section class="card flush">
      <div class="xl-h"><label><input type="checkbox" id="allck" ${ds.every(d => state.ready.has(d.id)) ? "checked" : ""}>全选</label><span class="sub" id="xc"></span></div>
      ${ds.map(d => `<label class="xr"><input type="checkbox" data-id="${d.id}" ${state.ready.has(d.id) ? "checked" : ""}>
        ${d.images.find(Boolean) ? `<img class="xthumb" src="/images/${esc(d.images.find(Boolean))}" data-imgs="${esc(JSON.stringify(d.images.filter(Boolean).map(i => "/images/" + i)))}">` : `<div class="ph-img">无图</div>`}
        <div class="xinfo" style="min-width:0" title="点击查看文案和配图"><div class="t">${esc(d.title)}</div><div class="sub">#${d.id}${d.auto_ok === 1 ? " · ✓ 合格" : d.auto_ok === 0 ? ` · ⚠ 需检查:${esc(d.auto_note || "")}` : ""} · ${d.images.filter(Boolean).length} 张图 · ${esc(d.tags.slice(0, 3).map(t => "#" + t).join(" "))}</div></div>
        <span class="row" style="gap:6px">${d.review?.grade ? gradeB(d.review.grade) : ""}${d.checks?.high ? `<span class="tier viral">高风险 ${d.checks.high}</span>` : ""}${d.checks?.placeholders?.length ? `<span class="tier normal">待补充 ${d.checks.placeholders.length}</span>` : ""}${st(d.status)}</span></label>`).join("")}
    </section>` : `<div class="empty"><b>还没有可导出的内容</b>先在「复刻」里生成笔记<br><button class="btn" data-go="generate">去复刻</button></div>`}
    <div class="note"><span>📦</span><div>导出结构:<code>批次/序号_标题/{title.txt, content.txt, images/}</code> + <code>清单.json</code><br>到小红书创作者中心逐篇复制粘贴并上传图片即可发布。</div></div>`;
  const count = () => {
    $("#xc") && ($("#xc").textContent = `已选 ${[...state.ready].filter(id => ds.some(d => d.id === id)).length} / ${ds.length}`); nav();
  };
  count();
  $("#main").onchange = e => {
    if (e.target.id === "allck") { ds.forEach(d => e.target.checked ? state.ready.add(d.id) : state.ready.delete(d.id)); $$(".xr input").forEach(i => i.checked = e.target.checked); return count(); }
    const id = +e.target.dataset.id; if (id) { e.target.checked ? state.ready.add(id) : state.ready.delete(id); count(); }
  };
  $("#acancel") && ($("#acancel").onclick = async () => { if (confirm("取消后已生成的稿子会保留,没跑完的步骤不再继续。确定取消?")) { await api(`/api/projects/${state.pid}/auto/cancel`, "POST"); vExport(); } });
  $("#sel").onclick = () => { state.ready = new Set(ds.filter(d => (a ? d.auto_ok === 1 : d.status === "ready")).map(d => d.id)); vExport(); };
  $("#pack").onclick = async () => {
    const ids = [...state.ready].filter(id => ds.some(d => d.id === id));
    const bad = ds.filter(d => ids.includes(d.id) && (d.checks?.high || d.checks?.combo?.length || d.checks?.placeholders?.length));
    if (bad.length && !confirm(`有 ${bad.length} 篇还有高风险词或没补充的【补充:…】占位符,发布前需要处理。仍然导出?`)) return;
    if (!ids.length) return toast("请先勾选要导出的内容", 1);
    try {
      const { zip } = await api("/api/export", "POST", { ids });
      $("#res").innerHTML = `<div class="banner ok">✓ 已导出:<a href="/download/${encodeURIComponent(zip)}">${esc(zip)}</a><span class="sub" style="color:inherit">(同时保存在 data/exports)</span></div>`;
    } catch (e) { toast(e.message, 1); }
  };
}

// ---------- 登录(左右分屏,见 login.js) ----------
async function gate() {
  if ($(".gate")) return;
  clearTimeout(pollT);
  let sm = false; try { sm = (await (await fetch("/api/session")).json()).staff_mode; } catch { }
  if ($(".gate")) return;
  let last = ""; try { last = localStorage.getItem("xhs_user") || ""; } catch { }
  loginScreen({ staffMode: sm, lastUser: last, submit: async ({ username, password, remember }) => {
    const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "登录失败");
    try { if (sm) remember ? localStorage.setItem("xhs_user", username) : localStorage.removeItem("xhs_user"); } catch { }
    setTimeout(() => { loadWho(); render(); });
  } });
}

// 侧栏底部:当前登录的员工和退出按钮(管理员预览或没开登录时不显示)
async function loadWho() {
  const el = $("#whoami"); if (!el) return;
  try {
    const s = await (await fetch("/api/session")).json();
    if (!s.user) { el.hidden = true; return; }
    el.hidden = false;
    el.innerHTML = `<span class="who-n" title="${esc(s.user.username)}">${esc(s.user.name)}</span><span><button class="who-x" id="pwBtn">改密码</button><button class="who-x" id="logoutBtn">退出</button></span>`;
    $("#logoutBtn").onclick = async () => { await fetch("/api/logout", { method: "POST" }); location.reload(); };
    $("#pwBtn").onclick = myPassword;
  } catch { }
}

// 员工改自己的密码:改完别的设备上的登录会失效,这台保持登录
function myPassword() {
  const m = document.createElement("div"); m.className = "mask";
  m.innerHTML = `<form class="card lp-dlg" style="max-width:380px"><div class="card-h"><b>修改密码</b><button type="button" class="btn ghost sm" data-x>关闭</button></div>
    <label class="f" for="pwo" style="margin-top:0">原密码</label><input id="pwo" type="password" autocomplete="current-password" required>
    <label class="f" for="pwn">新密码 <span class="sub">至少 6 位</span></label><input id="pwn" type="password" autocomplete="new-password" minlength="6" required>
    <label class="f" for="pwn2">再输一次新密码</label><input id="pwn2" type="password" autocomplete="new-password" minlength="6" required>
    <p class="sub" style="margin:10px 0 0">改完后,你在其他电脑、手机上的登录会自动退出。</p>
    <div class="row" style="margin-top:14px;justify-content:flex-end;gap:8px"><button type="button" class="btn gray" data-x>取消</button><button class="btn">保存</button></div></form>`;
  m.style.zIndex = 60; document.body.appendChild(m); $("#pwo").focus();
  m.onclick = e => { if (e.target === m || e.target.closest("[data-x]")) m.remove(); };
  m.querySelector("form").onsubmit = async e => {
    e.preventDefault();
    if ($("#pwn").value !== $("#pwn2").value) return toast("两次输入的新密码不一样", 1);
    try { await api("/api/me/password", "POST", { old: $("#pwo").value, new: $("#pwn").value }); m.remove(); toast("密码已修改"); }
    catch (err) { toast(err.message, 1); }
  };
}

// 左下角版本号:点一下复制「版本 · 提交 · 构建时间」,回滚 / 反馈问题时报给我
fetch("/api/version").then(r => r.json()).then(v => {
  const el = $("#ver"); if (!el) return;
  el.hidden = false; el.innerHTML = `<b>${esc(v.version)}${v.dirty ? "+" : ""}</b><span>${esc(v.commit || "本地开发")}${v.built ? " · " + esc(v.built.slice(5)) : ""}</span>`;
  const txt = `${v.version}${v.dirty ? "+(含未提交改动)" : ""} ${v.commit} ${v.built}`.trim();
  el.title = [txt, v.subject, "点击复制"].filter(Boolean).join("\n");
  el.onclick = () => copyText(txt).then(() => toast("已复制版本信息:" + txt), () => toast(txt));
}).catch(() => { });
fetch("/api/session").then(r => r.ok ? r.json() : Promise.reject()).then(s => s.ok ? (loadWho(), render()) : gate())
  .catch(() => { document.body.innerHTML = `<div class="gate"><div class="card" style="padding:32px;text-align:center;max-width:420px"><h2 style="margin:0 0 8px">服务端版本不匹配</h2><p class="sub">网页已更新,但服务还在运行旧版本。请关闭 run.bat 窗口后重新双击启动。</p></div></div>`; });
