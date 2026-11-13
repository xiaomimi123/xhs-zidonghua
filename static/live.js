// ---------- Live 图:从成品库选图 / 上传图片 → 排队 →(真实动作:本机显卡先生成视频)→ Mac 端做成实况 → 下载 .pvt 包 AirDrop 到 iPhone ----------
// 服务端只管队列和文件;视频由装了 ComfyUI 的电脑生成(tools/livephoto/video_worker.py),
// 实况的动画渲染和 .pvt 打包在 MacBook 上跑(tools/livephoto/mac_worker.py)。
const LV = { tab: "", lib: [], runs: [], motions: [], scenes: [], prods: [], bgItems: [], bgDefault: [], upMode: "up", jf: "all", who: "", showHow: null,
  bg: { files: [], items: null, extra: "", scenes: new Set(), custom: "" }, up: { files: [] }, gen: { files: [], product: 0, scene: "", note: "", count: 1 },
  pid: 0, todo: false, page: 1, sel: new Map(), jobs: [], st: null, timer: 0,
  opts: Object.assign({ mode: "motion", motion: "sway", seconds: 5, zoom: 1.08, focus_y: 0.35, fit: "crop" }, (() => { try { return JSON.parse(localStorage.lvOpts || "{}"); } catch { return {}; } })()) };
try { LV.upMode = localStorage.lvUpMode || "up"; } catch { }
const LV_FIT = { crop: "裁成 3:4", blur: "完整+虚化补边" };
const LV_VIDEO_SEC = 90, LV_GEN_SEC = 45;   // 估算耗时用:每次 AI 生图约 45 秒;每条视频约 90 秒
const lvKey = (d, img) => `${d}:${img}`;
// 经验:图里有「正在倒、撒、研磨」的动作(粉末、液体)时,AI 容易把粉末画成球状颗粒、越堆越多
const LV_TIP = "图里有倒、撒、研磨这类<b>正在进行的动作</b>(粉末、液体)时,AI 容易把粉末画成球状颗粒。建议选<b>静态画面</b>(手拿罐子、俯拍、搅拌);一定要用就选「慢推镜头」,或把「每张拍几条」调高,挑最好的一条。";
const lvMotionLabel = k => (LV.motions.find(m => m.key === k) || {}).label || k || "";

// 任务状态:按「阶段」说清楚现在卡在哪一台机器上
function lvState(j) {
  const v = j.mode === "motion";
  if (j.status === "done") return ["已完成", "ok"];
  if (j.status === "error") { const m = { image: "场景图失败", bg: "换背景失败", mask: "合成失败", video: "视频失败" }; return [(j.stage === "video" && !v ? "失败" : m[j.stage]) || "失败", "bad"]; }
  if (j.status === "running") return [{ image: "生成场景图中", bg: "AI 换背景中", mask: "抠主体合成中", video: "生成视频中" }[j.stage] || (v ? "Mac 制作实况中" : "Mac 制作中"), "run"];
  return [{ image: "排队 · 等待生成场景图", bg: "排队 · 等待换背景", mask: "排队 · 等待合成主体", video: "排队 · 等待生成视频" }[j.stage] || (v ? "视频已好 · 等待 Mac" : "排队中"), "warn"];
}
// 一个任务要经过哪几步,当前在第几步(给任务列表画进度条)
function lvFlow(j) {
  const names = { image: "场景图", bg: "换背景", mask: "合成", video: "视频", live: "实况" };
  const steps = [...(j.src === "gen" ? ["image"] : j.src === "bgswap" ? ["bg", "mask"] : []), ...(j.mode === "motion" ? ["video"] : []), "live"];
  const at = j.status === "done" ? steps.length : Math.max(0, steps.indexOf(j.stage));
  return steps.map((k, i) => ({ name: names[k], cls: i < at ? "done" : i === at ? (j.status === "error" ? "bad" : j.status === "running" ? "cur" : "wait") : "" }));
}

async function fLive() {
  clearTimeout(LV.timer);
  let sc, sj;
  [LV.lib, LV.runs, LV.st, LV.jobs, LV.motions, sc, LV.prods, sj] = await Promise.all([api("/api/library?brief=1"), api("/api/runs?limit=200").catch(() => []),
    api("/api/live/status").catch(() => null), lvFetchJobs().then(() => LV.jobs).catch(() => []), api("/api/live/motions").catch(() => []),
    api("/api/live/scenes").catch(() => ({ scenes: [] })), api("/api/products").catch(() => []),
    api("/api/live/bg_options").catch(() => ({ items: [], default: [] }))]);
  LV.scenes = sc.scenes || []; LV.bgItems = sj.items || []; LV.bgDefault = sj.default || [];
  if (!LV.bg.items) LV.bg.items = new Set(LV.bgDefault);
  LV.lib = LV.lib.filter(d => d.images.length);
  if (!LV.motions.some(m => m.key === LV.opts.motion)) LV.opts.motion = (LV.motions[0] || {}).key || "sway";
  if (!LV.tab) LV.tab = LV.sum?.all ? "jobs" : "pick";   // 已经有任务就先看任务,不用翻过长长的选图列表
  $("#main").innerHTML = `<div id="lvst"></div>
    <div class="seg lv-seg" role="tablist" id="lvseg"><button role="tab" data-t="pick">选图</button><button role="tab" data-t="up">上传</button><button role="tab" data-t="jobs">任务<em id="lvn"></em></button></div>
    <section id="lvpick"><div class="lv-h"><h3>从成品库选图</h3><span class="sub">点图片选中,一张图 = 一个 Live 图;封面最常用</span>
      <span class="lv-f"><button class="chip ${LV.todo ? "on" : ""}" id="lvtodo">只看未发布</button>
      <select id="lvrun" aria-label="按任务筛选"><option value="0">全部任务</option>${LV.runs.map(r => `<option value="${r.id}" ${LV.pid === r.id ? "selected" : ""}>${esc(r.name)}</option>`).join("")}</select></span></div>
      <details class="lv-tipd"><summary>选图小提示</summary><p>${LV_TIP}</p></details>
    <div id="lvgrid"></div></section>
    <section id="lvup" hidden></section>
    <section id="lvtasks"><div class="lv-h"><h3>任务</h3><span class="sub" id="lvsum"></span><span class="lv-f"><button class="btn gray sm" id="lvclear">清除已完成</button></span></div>
    <div class="lv-filter" id="lvfilter" role="group" aria-label="按状态筛选"></div>
    <div id="lvjobs"></div></section><div id="lvbar"></div>`;
  $("#lvseg").onclick = e => { const b = e.target.closest("[data-t]"); if (b) lvTab(b.dataset.t); };
  $("#lvtodo").onclick = () => { LV.todo = !LV.todo; LV.page = 1; $("#lvtodo").classList.toggle("on", LV.todo); lvGrid(); };
  $("#lvrun").onchange = e => { LV.pid = +e.target.value; LV.page = 1; lvGrid(); };
  $("#lvclear").onclick = async () => {
    if (!LV.sum?.done) return toast("没有已完成的任务");
    if (!confirm("清除全部已完成的任务和成品文件?(已经传到 iPhone 的就不需要留着了)")) return;
    try { const r = await api("/api/live/jobs/clear_done", "POST"); toast(`已清除 ${r.deleted} 个`); lvRefresh(); } catch (e) { toast(e.message, 1); }
  };
  $("#lvfilter").onclick = e => { const b = e.target.closest("[data-jf]"), w = e.target.closest("[data-jw]"); if (b) { LV.jf = b.dataset.jf; LV.jp = 1; lvRefresh(); } else if (w) { LV.who = w.dataset.jw; LV.jp = 1; lvRefresh(); } };
  $("#lvjobs").addEventListener("click", e => { const g = e.target.closest("[data-jpg]"); if (g && !g.disabled) { LV.jp = +g.dataset.jpg; lvRefresh().then(() => $("#lvtasks")?.scrollIntoView({ block: "start" })); } });
  $("#lvgrid").onclick = e => {
    const pg = e.target.closest("[data-pg]"); if (pg) { if (pg.disabled) return; LV.page = +pg.dataset.pg; lvGrid(); return $("#lvgrid").scrollIntoView({ behavior: "smooth", block: "start" }); }
    const t = e.target.closest("[data-k]"); if (!t) return;
    const [d, img] = [+t.dataset.d, t.dataset.img], k = lvKey(d, img);
    LV.sel.has(k) ? LV.sel.delete(k) : LV.sel.set(k, { draft_id: d, image: img });
    t.classList.toggle("on", LV.sel.has(k)); t.closest(".lv-nc").classList.toggle("picked", !!t.closest(".lv-nc").querySelector(".on"));
    lvBar();
  };
  $("#lvjobs").onclick = lvJobClick;
  lvGrid(); lvStatus(); lvJobs(); lvTab(LV.tab); LV.timer = setTimeout(lvRefresh, 3000);   // 刚取过状态和任务,不用马上再取一遍
}

function lvTab(t) {
  LV.tab = t;
  $("#lvpick").hidden = t !== "pick"; $("#lvtasks").hidden = t !== "jobs"; $("#lvup").hidden = t !== "up";
  if (t === "up") lvUpload();
  $$("#lvseg [data-t]").forEach(b => { b.classList.toggle("on", b.dataset.t === t); b.setAttribute("aria-selected", b.dataset.t === t); });
  lvBar();
}

const LV_SIZE = 36;   // 选图页每页篇数
function lvGrid() {
  const ds = LV.lib.filter(d => (!LV.pid || d.pid === LV.pid) && (!LV.todo || !d.published));
  const pages = Math.max(1, Math.ceil(ds.length / LV_SIZE)), page = Math.min(Math.max(1, LV.page), pages);   // 筛选后页数变少时落到最后一页
  const shown = ds.slice((page - 1) * LV_SIZE, page * LV_SIZE);
  $("#lvgrid").innerHTML = ds.length ? `<div class="fl-grid lv-grid">${shown.map(d => {
    const on = i => LV.sel.has(lvKey(d.id, i));
    return `<article class="fl-nc lv-nc ${d.images.some(on) ? "picked" : ""}">
      <div class="fl-cv lv-cv ${on(d.images[0]) ? "on" : ""}" data-k data-d="${d.id}" data-img="${esc(d.images[0])}" tabindex="0" role="checkbox" aria-checked="${on(d.images[0])}" aria-label="选择封面"><img src="${esc(thumb("/images/" + d.images[0]))}" alt="" loading="lazy" decoding="async"><span class="fl-sel" aria-hidden="true"></span></div>
      ${d.images.length > 1 ? `<div class="lv-th">${d.images.slice(1).map((i, n) => `<button class="lv-t ${on(i) ? "on" : ""}" data-k data-d="${d.id}" data-img="${esc(i)}" aria-label="选择第 ${n + 2} 张"><img src="${esc(thumb("/images/" + i, 240))}" alt="" loading="lazy" decoding="async"></button>`).join("")}</div>` : ""}
      <div class="fl-nt">${esc(d.title)}</div>
      <div class="fl-ns"><span>${esc(d.run)}</span>${d.published ? `<span class="pub">已发布</span>` : ""}</div></article>`;
  }).join("")}</div>${pager(page, ds.length, LV_SIZE)}`
    : `<div class="empty"><b>${LV.lib.length ? "这个筛选下没有带图的笔记" : "还没有成品"}</b>${LV.lib.length ? "换个筛选看看" : "先新建任务做出笔记,再来做 Live 图;也可以到「上传」里直接传自己的图"}</div>`;
  $("#lvgrid").onkeydown = e => { if ((e.key === "Enter" || e.key === " ") && e.target.matches(".lv-cv")) { e.preventDefault(); e.target.click(); } };
}

// 效果和参数的选项:选图页底部操作条、上传页的输出设置面板共用
function lvCtl() {
  const o = LV.opts, motion = o.mode === "motion";
  const opt = (v, cur, label) => `<option value="${v}" ${+cur === v ? "selected" : ""}>${label}</option>`;
  const cur = motion ? "motion:" + o.motion : "zoom";
  const m = LV.motions.find(x => x.key === o.motion);
  return `<label class="lv-o">效果 <select data-o="effect">${LV.motions.map(x => `<option value="motion:${x.key}" ${cur === "motion:" + x.key ? "selected" : ""}>真实动作 · ${esc(x.label)}</option>`).join("")}<option value="zoom" ${cur === "zoom" ? "selected" : ""}>慢推镜头(快,不用显卡)</option></select></label>
    ${motion ? `<label class="lv-o" title="每条的随机种子不同,同一张图多拍几条,挑效果最好的">每张拍 <select data-o="takes">${[1, 2, 3, 4].map(n => `<option value="${n}" ${(+o.takes || 1) === n ? "selected" : ""}>${n} 条</option>`).join("")}</select></label>
    <span class="lv-hint" title="${esc(m ? m.desc : "")}">由本机显卡生成,约 85 秒/条${m ? ` · ${esc(m.desc)}` : ""}</span>` : `
    <label class="lv-o">推近 <select data-o="zoom">${opt(1.05, o.zoom, "轻微")}${opt(1.08, o.zoom, "标准")}${opt(1.15, o.zoom, "明显")}</select></label>
    <label class="lv-o">焦点 <select data-o="focus_y">${opt(0.3, o.focus_y, "偏上")}${opt(0.35, o.focus_y, "略偏上")}${opt(0.5, o.focus_y, "居中")}${opt(0.7, o.focus_y, "偏下")}</select></label>
    <label class="lv-o">时长 <select data-o="seconds">${opt(3, o.seconds, "3 秒")}${opt(5, o.seconds, "5 秒")}</select></label>`}
    <label class="lv-o" title="图片不是 3:4 时怎么处理">非 3:4 的图 <select data-o="fit"><option value="crop" ${o.fit === "crop" ? "selected" : ""}>裁成 3:4</option><option value="blur" ${o.fit === "blur" ? "selected" : ""}>完整显示+虚化补边</option></select></label>`;
}
// 改了效果:所有显示选项的地方一起刷新
function lvCtlChange(e) {
  const k = e.target.dataset.o; if (!k) return;
  if (k === "effect") { const [md, mk] = e.target.value.split(":"); LV.opts.mode = md; if (mk) LV.opts.motion = mk; }
  else LV.opts[k] = k === "fit" ? e.target.value : +e.target.value;
  try { localStorage.lvOpts = JSON.stringify(LV.opts); } catch { }
  if (k === "effect" || k === "takes") { $$("[data-ctl]").forEach(el => el.innerHTML = lvCtl()); lvBar(); }
  if (LV.tab === "up") lvUpSync();
}

function lvBar() {
  const n = LV.tab === "pick" ? LV.sel.size : 0;
  document.body.classList.toggle("fl-picking", n > 0);
  $("#lvbar").innerHTML = n ? `<div class="fl-pickbar lv-bar" role="toolbar" aria-label="Live 图设置"><span>已选 <b>${n}</b> 张</span>
    <span class="lv-ctl" data-ctl>${lvCtl()}</span>
    <button class="btn gray sm" id="lvoff">清空</button><button class="btn" id="lvgo">做成 Live 图</button></div>` : "";
  if (!n) return;
  $("#lvbar").onchange = lvCtlChange;
  $("#lvoff").onclick = () => { LV.sel.clear(); lvGrid(); lvBar(); };
  $("#lvgo").onclick = async () => {
    const btn = $("#lvgo"); btn.disabled = true;
    try {
      const r = await api("/api/live/jobs", "POST", { items: [...LV.sel.values()], opts: LV.opts, takes: LV.opts.mode === "motion" ? +LV.opts.takes || 1 : 1 });
      toast(r.created ? `已提交 ${r.created} 个任务${r.skipped ? `,${r.skipped} 个已存在` : ""}` : `这 ${r.skipped} 张已经有同样参数的任务了`);
      LV.sel.clear(); lvGrid(); lvTab("jobs"); await lvRefresh(); window.scrollTo(0, 0);
    } catch (e) { toast(e.message, 1); btn.disabled = false; }
  };
}

// ---------- 上传:三种做法 ----------
const lvThumbs = (files, key) => files.length ? files.map((f, i) => `<span class="lv-pv"><img src="${esc(f.url)}" alt=""><button data-rm="${key}:${i}" aria-label="移除">×</button></span>`).join("") : "";
async function lvPick(files, bucket, max) {
  const room = max - bucket.length; if (room <= 0) return toast(`最多 ${max} 张`, 1);
  for (const f of [...files].slice(0, room)) {
    if (!/^image\//.test(f.type)) { toast(`${f.name} 不是图片`, 1); continue; }
    try { bucket.push({ name: f.name, url: await shrink(f) }); } catch { toast(`${f.name} 读取失败`, 1); }
  }
}
const LV_MODES = [
  ["up", "直接上传", "用你自己的图原样做成 Live"],
  ["bg", "换背景", "实拍图换成别的场景,主体原样不变"],
  ["gen", "产品图生成场景", "AI 按产品图重画一张随手拍场景图"],
];
// 这次要提交什么:几个任务、几次 AI 生图、约多少钱、约多久、还差什么
function lvPlan() {
  const o = LV.opts, motion = o.mode === "motion", takes = motion ? Math.min(4, Math.max(1, +o.takes || 1)) : 1;
  const m = LV.upMode, G = LV.gen, B = LV.bg;
  let imgs = 0, tasks = 0, gen = 0, need = "";
  if (m === "up") { imgs = LV.up.files.length; tasks = imgs * takes; if (!imgs) need = "先上传图片"; }
  else if (m === "bg") {
    imgs = B.files.length; const sc = Math.max(1, Math.min(4, B.scenes.size + (B.custom.trim() ? 1 : 0)));
    tasks = imgs * sc * takes; gen = imgs * sc; if (!imgs) need = "先上传实拍图"; else if (!B.items.size && !B.extra.trim()) need = "选一下背景是什么";
  } else {
    tasks = G.count; gen = G.count; if (!(G.product || G.files.length)) need = "先选产品或上传产品图";
  }
  const sec = tasks * (motion ? LV_VIDEO_SEC : 12) + Math.ceil(gen / 2) * LV_GEN_SEC;
  return { tasks, gen, mins: Math.max(1, Math.round(sec / 60)), need, takes, motion };
}
// 右侧面板的汇总和提交按钮(不重画整个表单,避免打字时丢焦点)
function lvUpSync() {
  const p = lvPlan(), box = $("#lvsum2"), btn = $("#lvsubmit"); if (!box || !btn) return;
  box.innerHTML = p.need ? `<p class="lv-need">${p.need}</p>` : `<dl><div><dt>将生成</dt><dd><b>${p.tasks}</b> 个 Live 任务</dd></div>
    ${p.gen ? `<div><dt>AI 生图</dt><dd><b>${p.gen}</b> 张</dd></div>` : ""}
    <div><dt>预计耗时</dt><dd>约 ${p.mins} 分钟</dd></div></dl>`;
  btn.disabled = !!p.need; btn.textContent = p.need ? "提交" : `提交 ${p.tasks} 个任务`;
}
function lvUpload() {
  const sec = $("#lvup"), G = LV.gen, B = LV.bg, m = LV.upMode, prod = LV.prods.find(p => p.id === G.product);
  const drop = (id, text) => `<label class="lv-drop" id="${id}" tabindex="0"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M12 16V5m0 0l-4 4m4-4l4 4M5 19h14"/></svg><span>${text}</span><input type="file" accept="image/*" multiple hidden></label>`;
  const panel = {
    up: `<h3>上传图片</h3><p class="sub">图片原样作为源图,不经过生图模型。建议 3:4 竖图、画面清晰、包装上的字清楚;不是 3:4 的按右边「非 3:4 的图」处理。</p>
      ${drop("lvdrop1", "点击选择图片,或拖到这里(最多 9 张)")}<div class="lv-pvs" id="lvpv1">${lvThumbs(LV.up.files, "up")}</div>`,
    bg: `<h3>实拍图换背景</h3><p class="sub">告诉我背景是什么,把它换成别的场景;图里其余的一切(手、产品、包装文字、杯子、勺子…)都<b>保持原图不变</b>。同一张图可以换多个背景,重复利用。</p>
      ${drop("lvdrop3", "点击选择实拍图,或拖到这里(最多 3 张)")}<div class="lv-pvs" id="lvpv3">${lvThumbs(B.files, "bg")}</div>
      <label class="f">背景是什么 <span class="sub">要换掉的部分;没选到的背景会保留原样</span></label>
      <div class="lv-chips" id="lvsubj">${LV.bgItems.map(x => `<button type="button" class="lv-chip ${B.items.has(x.key) ? "on" : ""}" data-bgi="${esc(x.key)}">${esc(x.label)}</button>`).join("")}</div>
      <input id="lvsubx" placeholder="补充背景(英文名词,逗号分隔,可选),例如:sofa, grass" value="${esc(B.extra)}">
      <label class="f">换成什么背景 <span class="sub">可多选,最多 4 个;不选 = 自动挑一个生活场景</span></label>
      <div class="lv-chips lv-scn" id="lvscn">${LV.scenes.map(s => `<button type="button" class="lv-chip ${B.scenes.has(s) ? "on" : ""}" data-scn="${esc(s)}">${esc(s)}</button>`).join("")}</div>
      <input id="lvscx" maxlength="80" placeholder="或自己写一个背景,例如:阳台木桌,午后阳光" value="${esc(B.custom)}">`,
    gen: `<h3>产品图 → 随手拍场景图</h3><p class="sub">用你的产品图当参考,让生图模型画一张手机随手拍风格的生活场景图,产品外观和包装文字尽量保持不变。<b>整张图都是 AI 重画的</b>;想保留实拍的原图,请用「换背景」。</p>
      <label class="f">产品</label>
      <select id="lvprod"><option value="0">上传新的产品图</option>${LV.prods.filter(p => p.images.length).map(p => `<option value="${p.id}" ${G.product === p.id ? "selected" : ""}>${esc(p.name)}(${p.images.length} 张图)</option>`).join("")}</select>
      ${prod ? `<div class="lv-pvs">${prod.images.slice(0, 3).map(u => `<span class="lv-pv"><img src="${esc(u)}" alt=""></span>`).join("")}</div>`
        : `${drop("lvdrop2", "点击选择产品图,或拖到这里(1~3 张,包装正面最重要)")}<div class="lv-pvs" id="lvpv2">${lvThumbs(G.files, "gen")}</div>`}
      <div class="lv-two"><div><label class="f">场景</label><select id="lvscene"><option value="">自动(每张换一个生活场景)</option>${LV.scenes.map(s => `<option value="${esc(s)}" ${G.scene === s ? "selected" : ""}>${esc(s)}</option>`).join("")}</select></div>
        <div><label class="f">生成几张</label><select id="lvcount">${[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(n => `<option value="${n}" ${G.count === n ? "selected" : ""}>${n} 张</option>`).join("")}</select></div></div>
      <label class="f">画面补充 <span class="sub">可选</span></label><input id="lvnote" maxlength="100" placeholder="例如:一只手拿着罐子、旁边有一杯刚冲好的拿铁" value="${esc(G.note)}">`,
  }[m];
  sec.innerHTML = `<details class="lv-tipd"><summary>选图小提示</summary><p>${LV_TIP}</p></details>
    <div class="lv-modes" role="tablist" aria-label="上传方式">${LV_MODES.map(([k, t, d]) => `<button role="tab" class="lv-mode ${k === m ? "on" : ""}" data-m="${k}" aria-selected="${k === m}"><b>${t}</b><span>${d}</span></button>`).join("")}</div>
    <div class="lv-form"><div class="card lv-src">${panel}</div>
      <aside class="card lv-side" aria-label="输出设置"><h3>输出设置</h3>
        <div class="lv-fields" data-ctl>${lvCtl()}</div>
        <div class="lv-sum" id="lvsum2" aria-live="polite"></div>
        <button class="btn lg" id="lvsubmit" disabled>提交</button></aside></div>`;
  lvUpSync();
  const wireDrop = (id, bucket, max) => {
    const el = $("#" + id); if (!el) return;
    el.querySelector("input").onchange = async e => { await lvPick(e.target.files, bucket, max); lvUpload(); };
    el.ondragover = e => { e.preventDefault(); el.classList.add("over"); };
    el.ondragleave = () => el.classList.remove("over");
    el.ondrop = async e => { e.preventDefault(); el.classList.remove("over"); await lvPick(e.dataTransfer.files, bucket, max); lvUpload(); };
    el.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el.querySelector("input").click(); } };
  };
  wireDrop("lvdrop1", LV.up.files, 9); wireDrop("lvdrop2", G.files, 3); wireDrop("lvdrop3", B.files, 3);
  sec.onclick = e => {
    const md = e.target.closest("[data-m]");
    if (md) { LV.upMode = md.dataset.m; try { localStorage.lvUpMode = LV.upMode; } catch { } return lvUpload(); }
    const r = e.target.closest("[data-rm]");
    if (r) { const [k, i] = r.dataset.rm.split(":"); ({ up: LV.up.files, gen: G.files, bg: B.files })[k].splice(+i, 1); return lvUpload(); }
    const sj = e.target.closest("[data-bgi]");
    if (sj) { const k = sj.dataset.bgi; B.items.has(k) ? B.items.delete(k) : B.items.add(k); sj.classList.toggle("on", B.items.has(k)); return lvUpSync(); }
    const sc = e.target.closest("[data-scn]");
    if (sc) {
      const k = sc.dataset.scn;
      if (!B.scenes.has(k) && B.scenes.size >= 4) return toast("最多选 4 个背景", 1);
      B.scenes.has(k) ? B.scenes.delete(k) : B.scenes.add(k); sc.classList.toggle("on", B.scenes.has(k)); return lvUpSync();
    }
  };
  sec.onchange = e => {
    if (e.target.dataset.o) return lvCtlChange(e);
    if (e.target.id === "lvprod") { G.product = +e.target.value; return lvUpload(); }
    if (e.target.id === "lvscene") G.scene = e.target.value;
    if (e.target.id === "lvcount") { G.count = +e.target.value; lvUpSync(); }
  };
  sec.oninput = e => {
    if (e.target.id === "lvnote") G.note = e.target.value;
    if (e.target.id === "lvsubx") { B.extra = e.target.value; lvUpSync(); }
    if (e.target.id === "lvscx") { B.custom = e.target.value; lvUpSync(); }
  };
  $("#lvsubmit").onclick = lvSubmit;
}
async function lvSubmit() {
  const btn = $("#lvsubmit"), m = LV.upMode, G = LV.gen, B = LV.bg, takes = LV.opts.mode === "motion" ? +LV.opts.takes || 1 : 1; btn.disabled = true;
  try {
    let r;
    if (m === "up") {
      r = await api("/api/live/jobs/upload", "POST", { images: LV.up.files.map(f => f.url), names: LV.up.files.map(f => f.name), opts: LV.opts, takes });
      LV.up.files = [];
    } else if (m === "bg") {
      const scenes = [...B.scenes, ...(B.custom.trim() ? [B.custom.trim()] : [])].slice(0, 4);
      const background = [...B.items, ...B.extra.split(/[,,]/).map(x => x.trim()).filter(Boolean)];
      r = await api("/api/live/jobs/bgswap", "POST", { images: B.files.map(f => f.url), names: B.files.map(f => f.name), scenes, background, opts: LV.opts, takes });
      B.files = [];
    } else {
      const body = { count: G.count, scene: G.scene, note: G.note, opts: LV.opts };
      if (G.product) body.product_id = G.product; else body.images = G.files.map(f => f.url);
      r = await api("/api/live/jobs/generate", "POST", body);
      G.files = [];
    }
    toast(r.created ? `已提交 ${r.created} 个任务${r.skipped ? `,${r.skipped} 个已存在` : ""}` : `这些已经有同样参数的任务了`);
    lvTab("jobs"); await lvRefresh(); window.scrollTo(0, 0);
  } catch (e) { toast(e.message, 1); btn.disabled = false; }
}

// ---------- 状态条 + 任务列表(每 3 秒刷新) ----------
async function lvRefresh() {
  clearTimeout(LV.timer);
  if (state.tab !== "live" || !$("#lvjobs")) return;
  try { [LV.st] = await Promise.all([api("/api/live/status"), lvFetchJobs()]); } catch (e) { if (!LV.st) { $("#lvst").innerHTML = `<div class="banner err">${esc(e.message)}</div>`; } }
  if (state.tab !== "live" || !$("#lvjobs")) return;
  lvStatus(); lvJobs();
  LV.timer = setTimeout(lvRefresh, 3000);
}

const lvAgo = s => s < 90 ? `${s} 秒前` : s < 5400 ? `${Math.round(s / 60)} 分钟前` : s < 172800 ? `${Math.round(s / 3600)} 小时前` : `${Math.round(s / 86400)} 天前`;
// 机器状态:一行三个小标签(Mac / 视频机 / AI 生图),详细说明收进「怎么启动」
function lvChip(label, w, online, queue, tip) {
  const x = w && w.workers && w.workers[0];
  const st = online ? "ok" : "off", sub = online ? `${esc(x.worker)}` : x ? `离线 ${lvAgo(x.ago)}` : "未连接";
  return `<span class="lv-m ${st}" title="${esc(tip)}"><i aria-hidden="true"></i><b>${label}</b><span>${sub}</span>${queue ? `<em>${queue}</em>` : ""}</span>`;
}
function lvStatus() {
  const s = LV.st; if (!s) return;
  const v = s.video || { workers: [], online: false, pending: 0, running: 0 }, im = s.image || { pending: 0, running: 0 };
  const macQ = s.pending + s.running ? `排队 ${s.pending} · 制作 ${s.running}` : "", vQ = [v.pending + v.running ? `视频 ${v.running}/${v.pending}` : "", (v.mask_pending || 0) + (v.mask_running || 0) ? `合成 ${v.mask_running || 0}/${v.mask_pending || 0}` : ""].filter(Boolean).join(" · ");
  const open = LV.showHow ?? !(s.workers[0] || v.workers[0]), base = location.origin;
  $("#lvst").innerHTML = `<div class="lv-mach">${lvChip("Mac", s, s.online, macQ, "做成实况 .pvt 包(MacBook)")}${lvChip("视频机", v, v.online, vQ, "本机显卡:生成视频、抠主体合成(真实动作 / 换背景需要)")}
    <span class="lv-m ${im.running ? "busy" : "ok"}" title="服务端调用 gpt-image-2:场景图 / 改背景"><i aria-hidden="true"></i><b>AI 生图</b><span>${im.running ? `进行中 ${im.running}` : "空闲"}</span>${im.pending ? `<em>排队 ${im.pending}</em>` : ""}</span>
    <button class="lv-how-b" id="lvhowb" aria-expanded="${open}">怎么启动 ${open ? "▴" : "▾"}</button></div>
    <div class="lv-how" id="lvhow" ${open ? "" : "hidden"}>
      <p class="sub">任务可以先提交,会一直排队,等对应的程序启动后自动开始做。<b>Token</b> 在管理端「接口配置」里复制采集端 Token。</p>
      <ol><li><b>视频机</b>(装了 ComfyUI + MiniMax H3 + SAM3 的电脑,「真实动作」和「换背景」需要),先启动 ComfyUI,再在 <code>tools/livephoto</code> 里运行:<pre>python video_worker.py --server ${esc(base)} --token 你的Token</pre></li>
      <li><b>Mac</b>:把 <code>tools/livephoto</code> 拷到 MacBook,装一次依赖 <code>pip3 install -r requirements-mac.txt</code>,再运行:<pre>python3 mac_worker.py --server ${esc(base)} --token 你的Token</pre></li>
      <li>做好的 <code>.pvt</code> 包存在 Mac 的 <code>~/LivePhotos</code>,同时回传到这里。双击 <code>.pvt</code> 导入「照片」,再 AirDrop 到 iPhone;或在「任务」里「下载」后解压。</li></ol>
      <p class="sub">流程:真实动作 = 先在视频机上由本机显卡生成视频 → 再由 Mac 做成实况;慢推镜头 = 只需要 Mac。Windows 上没法做实况:必须由 macOS 写入系统识别的配对信息。</p></div>`;
  $("#lvhowb").onclick = () => { LV.showHow = !open; $("#lvhow").hidden = !LV.showHow; $("#lvhowb").textContent = `怎么启动 ${LV.showHow ? "▴" : "▾"}`; $("#lvhowb").setAttribute("aria-expanded", LV.showHow); };
}

// 任务列表按页从服务端取(状态、发起人也在服务端筛):排队的任务很多时,不会把已完成的挤出列表
const LV_JSIZE = 30;
async function lvFetchJobs() {
  const by = encodeURIComponent(LV.who || ""), st = LV.jf === "all" ? "" : LV.jf;
  const [jobs, sum] = await Promise.all([api(`/api/live/jobs?status=${st}&by=${by}&limit=${LV_JSIZE}&offset=${((LV.jp || 1) - 1) * LV_JSIZE}`), api(`/api/live/jobs/summary?by=${by}`)]);
  LV.jobs = jobs; LV.sum = sum;
}
function lvJobs() {
  const sm = LV.sum || { all: 0, run: 0, done: 0, err: 0, who: {} }, all = LV.jobs, act = sm.run;
  const cnt = { all: sm.all, run: sm.run, done: sm.done, err: sm.err };
  $("#lvsum").textContent = sm.all ? `共 ${sm.all} 个${act ? `,${act} 个进行中` : ""}` : "";
  const n = $("#lvn"); if (n) { n.textContent = act || sm.all || ""; n.className = act ? "run" : ""; n.title = act ? "进行中的任务数" : "任务总数"; }
  $("#lvfilter").innerHTML = sm.all || LV.who ? [["all", "全部"], ["run", "进行中"], ["done", "已完成"], ["err", "失败"]].map(([k, l]) => `<button class="chip ${LV.jf === k ? "on" : ""}" data-jf="${k}" ${cnt[k] ? "" : "disabled"}>${l} ${cnt[k]}</button>`).join("") : "";
  if (LV.jf !== "all" && !cnt[LV.jf]) { LV.jf = "all"; LV.jp = 1; return lvRefresh(); }   // 这个状态已经没有任务了,回到「全部」
  const who = Object.keys(sm.who || {});   // 管理员能看到所有人的任务:有多个发起人时,每条标出是谁做的,并能按人筛选
  if (LV.who && !who.includes(LV.who)) { LV.who = ""; LV.jp = 1; return lvRefresh(); }
  const js = all;
  if (!js.length && LV.jp > 1) { LV.jp = 1; return lvRefresh(); }   // 数据变少,当前页已经不存在
  $("#lvfilter").innerHTML += who.length > 1 ? `<span class="sub" style="margin-left:8px">发起人</span>` + [["", "全部"], ...who.map(w => [w, w])].map(([k, l]) => `<button class="chip ${LV.who === k ? "on" : ""}" data-jw="${esc(k)}">${esc(l)}</button>`).join("") : "";
  $("#lvjobs").innerHTML = js.length ? `<div class="lv-jobs">${js.map(j => {
    const [lab, cls] = lvState(j), o = j.opts || {}, motion = j.mode === "motion";
    const how = motion ? `真实动作 · ${esc(j.motion || lvMotionLabel(o.motion))}${o.take ? ` · 第 ${o.take} 条` : ""} · ${o.seconds || 3} 秒` : `慢推镜头 · 推近 ${Math.round(((o.zoom || 1) - 1) * 100)}% · ${o.seconds || 5} 秒`;
    const from = j.src === "upload" ? "上传图片 · " : j.src === "gen" ? "产品场景图 · " : j.src === "bgswap" ? `换背景 · ${esc(j.scene || "自动场景")} · ` : "";
    const flow = lvFlow(j);
    return `<article class="lv-job" data-j="${j.id}">${j.thumb || j.image ? `<img src="${esc(thumb("/images/" + (j.thumb || j.image), 240))}" alt="" loading="lazy" decoding="async">` : `<div class="lv-ph" aria-hidden="true">场景图</div>`}
      <div class="lv-jt"><b>${esc(j.title)}</b><span class="sub">${from}${how} · ${esc(LV_FIT[o.fit] || "")} · ${esc(when(j.created))}${who.length > 1 ? ` · 发起:${esc(j.by || "管理员")}` : ""}${j.worker ? ` · ${esc(j.worker)}` : ""}${j.size ? ` · ${(j.size / 1048576).toFixed(1)} MB` : ""}</span>
        ${flow.length > 1 ? `<div class="lv-flow" aria-label="进度">${flow.map(f => `<span class="${f.cls}"><i></i>${f.name}</span>`).join("")}</div>` : ""}
        ${j.status === "error" ? `<span class="fl-err">${esc(j.error)}</span>` : ""}</div>
      <span class="fl-pill ${cls}">${lab}</span>
      <div class="lv-ja">${j.image && (j.src === "gen" || j.src === "bgswap") ? `<button class="btn gray sm" data-a="img" title="AI 生成 / 合成好的图">看新图</button>` : ""}${j.has_video ? `<button class="btn gray sm" data-a="video" title="生成的视频(还没做成实况)">看视频</button>` : ""}
        ${j.has_result ? `<button class="btn gray sm" data-a="play">预览实况</button><a class="btn gray sm" href="/api/live/jobs/${j.id}/download">下载</a>` : ""}
        ${j.status === "error" || j.status === "done" ? `<button class="btn ghost sm" data-a="retry" title="${motion && j.has_video ? "视频保留,只重做实况" : "按原参数重新生成"}">${j.status === "error" ? "重试" : "重做"}</button>` : ""}
        ${motion && j.has_video && j.status !== "running" ? `<button class="btn ghost sm" data-a="regen" title="丢掉这条视频,重新生成一条">重拍视频</button>` : ""}
        ${j.status !== "running" ? `<button class="btn ghost sm danger" data-a="del" aria-label="删除">删除</button>` : ""}</div></article>`;
  }).join("")}</div>${pager(LV.jp || 1, cnt[LV.jf], LV_JSIZE, "jpg")}` : sm.all ? `<div class="empty"><b>这个筛选下没有任务</b>换个状态看看</div>` : `<div class="empty"><b>还没有 Live 图任务</b>在「选图」里点选图片,或到「上传」里传自己的图,再点「做成 Live 图」</div>`;
}

async function lvJobClick(e) {
  const b = e.target.closest("[data-a]"); if (!b) return;
  const id = +b.closest("[data-j]").dataset.j, a = b.dataset.a;
  try {
    if (a === "img") { const j = LV.jobs.find(x => x.id === id); return j && lightbox(["/images/" + j.image], 0); }
    if (a === "play") return lvPlay(id, "preview");
    if (a === "video") return lvPlay(id, "video");
    if (a === "del") { if (!confirm("删除这个任务和它的视频、成品?")) return; await api(`/api/live/jobs/${id}`, "DELETE"); }
    if (a === "retry") await api(`/api/live/jobs/${id}/retry`, "POST");
    if (a === "regen") { if (!confirm("丢掉现有视频,重新生成一条?(会占用显卡几十秒)")) return; await api(`/api/live/jobs/${id}/retry?video=1`, "POST"); }
  } catch (err) { toast(err.message, 1); }
  lvRefresh();
}

function lvPlay(id, kind) {
  const m = document.createElement("div"); m.className = "lb";
  const close = () => { m.remove(); document.removeEventListener("keydown", key); };
  const key = e => { if (e.key === "Escape") close(); };
  m.innerHTML = `<video src="/api/live/jobs/${id}/${kind}" autoplay loop muted playsinline controls></video><button class="lb-c" data-c aria-label="关闭">✕</button>`;
  m.onclick = e => { if (e.target.tagName !== "VIDEO") close(); };
  document.addEventListener("keydown", key); document.body.appendChild(m);
}

// 成品预览里的「做成 Live 图」:当前看的这张图,用上次的参数直接下单
async function lvAdd(draftId, image) {
  try {
    const r = await api("/api/live/jobs", "POST", { items: [{ draft_id: draftId, image }], opts: LV.opts });
    toast(r.created ? "已加入 Live 图任务,到「Live 图」页查看" : "这张图已经有同样参数的 Live 图任务了");
  } catch (e) { toast(e.message, 1); }
}

window.FLOW_VIEWS.live = { t: "Live 图", d: "把笔记配图或自己的实拍图做成 iPhone 实况照片:真实动作(本机显卡生成视频)或慢推镜头,最后由 MacBook 做成实况", fn: fLive };
