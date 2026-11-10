// ---------- Live 图:从成品库选图 → 排队 → (真实动作:本机显卡先生成视频)→ Mac 端做成实况 → 下载 .pvt 包 AirDrop 到 iPhone ----------
// 服务端只管队列和文件;视频由装了 ComfyUI 的电脑生成(tools/livephoto/video_worker.py),
// 实况的动画渲染和 .pvt 打包在 MacBook 上跑(tools/livephoto/mac_worker.py)。
const LV = { tab: "", lib: [], runs: [], motions: [], pid: 0, todo: false, show: 60, sel: new Map(), jobs: [], st: null, timer: 0,
  opts: Object.assign({ mode: "motion", motion: "sway", seconds: 5, zoom: 1.08, focus_y: 0.35, fit: "crop" }, (() => { try { return JSON.parse(localStorage.lvOpts || "{}"); } catch { return {}; } })()) };
const LV_FIT = { crop: "裁成 3:4", blur: "完整+虚化补边" };
const lvKey = (d, img) => `${d}:${img}`;
const lvMotionLabel = k => (LV.motions.find(m => m.key === k) || {}).label || k || "";

// 任务状态:按「阶段」说清楚现在卡在哪一台机器上
function lvState(j) {
  const v = j.mode === "motion";
  if (j.status === "done") return ["已完成", "ok"];
  if (j.status === "error") return [v && j.stage === "video" ? "视频失败" : "失败", "bad"];
  if (j.status === "running") return [j.stage === "video" ? "生成视频中" : v ? "Mac 制作实况中" : "Mac 制作中", "run"];
  return [j.stage === "video" ? "排队 · 等待生成视频" : v ? "视频已好 · 等待 Mac" : "排队中", "warn"];
}

async function fLive() {
  clearTimeout(LV.timer);
  [LV.lib, LV.runs, LV.st, LV.jobs, LV.motions] = await Promise.all([api("/api/library"), api("/api/runs").catch(() => []),
    api("/api/live/status").catch(() => null), api("/api/live/jobs").catch(() => []), api("/api/live/motions").catch(() => [])]);
  LV.lib = LV.lib.filter(d => d.images.length);
  if (!LV.motions.some(m => m.key === LV.opts.motion)) LV.opts.motion = (LV.motions[0] || {}).key || "sway";
  if (!LV.tab) LV.tab = LV.jobs.length ? "jobs" : "pick";   // 已经有任务就先看任务,不用翻过长长的选图列表
  $("#main").innerHTML = `<div id="lvst"></div>
    <div class="seg lv-seg" role="tablist" id="lvseg"><button role="tab" data-t="pick">选图</button><button role="tab" data-t="jobs">任务<em id="lvn"></em></button></div>
    <section id="lvpick"><div class="lv-h"><h3>选图</h3><span class="sub">点图片选中,一张图 = 一个 Live 图;封面最常用</span>
      <span class="lv-f"><button class="chip ${LV.todo ? "on" : ""}" id="lvtodo">只看未发布</button>
      <select id="lvrun" aria-label="按任务筛选"><option value="0">全部任务</option>${LV.runs.map(r => `<option value="${r.id}" ${LV.pid === r.id ? "selected" : ""}>${esc(r.name)}</option>`).join("")}</select></span></div>
    <div id="lvgrid"></div></section>
    <section id="lvtasks"><div class="lv-h"><h3>任务</h3><span class="sub" id="lvsum"></span><span class="lv-f"><button class="btn gray sm" id="lvclear">清除已完成</button></span></div>
    <div id="lvjobs"></div></section><div id="lvbar"></div>`;
  $("#lvseg").onclick = e => { const b = e.target.closest("[data-t]"); if (b) lvTab(b.dataset.t); };
  $("#lvtodo").onclick = () => { LV.todo = !LV.todo; LV.show = 60; $("#lvtodo").classList.toggle("on", LV.todo); lvGrid(); };
  $("#lvrun").onchange = e => { LV.pid = +e.target.value; LV.show = 60; lvGrid(); };
  $("#lvclear").onclick = async () => {
    if (!LV.jobs.some(j => j.status === "done")) return toast("没有已完成的任务");
    if (!confirm("清除全部已完成的任务和成品文件?(已经传到 iPhone 的就不需要留着了)")) return;
    try { const r = await api("/api/live/jobs/clear_done", "POST"); toast(`已清除 ${r.deleted} 个`); lvRefresh(); } catch (e) { toast(e.message, 1); }
  };
  $("#lvgrid").onclick = e => {
    const more = e.target.closest("[data-more]"); if (more) { LV.show += 60; return lvGrid(); }
    const t = e.target.closest("[data-k]"); if (!t) return;
    const [d, img] = [+t.dataset.d, t.dataset.img], k = lvKey(d, img);
    LV.sel.has(k) ? LV.sel.delete(k) : LV.sel.set(k, { draft_id: d, image: img });
    t.classList.toggle("on", LV.sel.has(k)); t.closest(".lv-nc").classList.toggle("picked", !!t.closest(".lv-nc").querySelector(".on"));
    lvBar();
  };
  $("#lvjobs").onclick = lvJobClick;
  lvGrid(); lvStatus(); lvJobs(); lvTab(LV.tab); lvRefresh();
}

function lvTab(t) {
  LV.tab = t;
  $("#lvpick").hidden = t !== "pick"; $("#lvtasks").hidden = t !== "jobs";
  $$("#lvseg [data-t]").forEach(b => { b.classList.toggle("on", b.dataset.t === t); b.setAttribute("aria-selected", b.dataset.t === t); });
  lvBar();
}

function lvGrid() {
  const ds = LV.lib.filter(d => (!LV.pid || d.pid === LV.pid) && (!LV.todo || !d.published));
  const shown = ds.slice(0, LV.show);
  $("#lvgrid").innerHTML = ds.length ? `<div class="fl-grid lv-grid">${shown.map(d => {
    const on = i => LV.sel.has(lvKey(d.id, i));
    return `<article class="fl-nc lv-nc ${d.images.some(on) ? "picked" : ""}">
      <div class="fl-cv lv-cv ${on(d.images[0]) ? "on" : ""}" data-k data-d="${d.id}" data-img="${esc(d.images[0])}" tabindex="0" role="checkbox" aria-checked="${on(d.images[0])}" aria-label="选择封面"><img src="/images/${esc(d.images[0])}" alt="" loading="lazy"><span class="fl-sel" aria-hidden="true"></span></div>
      ${d.images.length > 1 ? `<div class="lv-th">${d.images.slice(1).map((i, n) => `<button class="lv-t ${on(i) ? "on" : ""}" data-k data-d="${d.id}" data-img="${esc(i)}" aria-label="选择第 ${n + 2} 张"><img src="/images/${esc(i)}" alt="" loading="lazy"></button>`).join("")}</div>` : ""}
      <div class="fl-nt">${esc(d.title)}</div>
      <div class="fl-ns"><span>${esc(d.run)}</span>${d.published ? `<span class="pub">已发布</span>` : ""}</div></article>`;
  }).join("")}</div>${ds.length > shown.length ? `<p style="text-align:center"><button class="btn gray" data-more>再显示 ${Math.min(60, ds.length - shown.length)} 篇(还有 ${ds.length - shown.length} 篇)</button></p>` : ""}`
    : `<div class="empty"><b>${LV.lib.length ? "这个筛选下没有带图的笔记" : "还没有成品"}</b>${LV.lib.length ? "换个筛选看看" : "先新建任务做出笔记,再来做 Live 图"}</div>`;
  $("#lvgrid").onkeydown = e => { if ((e.key === "Enter" || e.key === " ") && e.target.matches(".lv-cv")) { e.preventDefault(); e.target.click(); } };
}

function lvBar() {
  const n = LV.tab === "pick" ? LV.sel.size : 0, o = LV.opts, motion = o.mode === "motion";
  document.body.classList.toggle("fl-picking", n > 0);
  const opt = (v, cur, label) => `<option value="${v}" ${+cur === v ? "selected" : ""}>${label}</option>`;
  const cur = motion ? "motion:" + o.motion : "zoom";
  const m = LV.motions.find(x => x.key === o.motion);
  $("#lvbar").innerHTML = n ? `<div class="fl-pickbar lv-bar" role="toolbar" aria-label="Live 图设置"><span>已选 <b>${n}</b> 张</span>
    <label class="lv-o">效果 <select data-o="effect">${LV.motions.map(x => `<option value="motion:${x.key}" ${cur === "motion:" + x.key ? "selected" : ""}>真实动作 · ${esc(x.label)}</option>`).join("")}<option value="zoom" ${cur === "zoom" ? "selected" : ""}>慢推镜头(快,不用显卡)</option></select></label>
    ${motion ? `<span class="lv-hint" title="${esc(m ? m.desc : "")}">由本机显卡生成,约 40 秒/条${m ? ` · ${esc(m.desc)}` : ""}</span>` : `
    <label class="lv-o">推近 <select data-o="zoom">${opt(1.05, o.zoom, "轻微")}${opt(1.08, o.zoom, "标准")}${opt(1.15, o.zoom, "明显")}</select></label>
    <label class="lv-o">焦点 <select data-o="focus_y">${opt(0.3, o.focus_y, "偏上")}${opt(0.35, o.focus_y, "略偏上")}${opt(0.5, o.focus_y, "居中")}${opt(0.7, o.focus_y, "偏下")}</select></label>
    <label class="lv-o">时长 <select data-o="seconds">${opt(3, o.seconds, "3 秒")}${opt(5, o.seconds, "5 秒")}</select></label>`}
    <label class="lv-o" title="图片不是 3:4 时怎么处理">非 3:4 的图 <select data-o="fit"><option value="crop" ${o.fit === "crop" ? "selected" : ""}>裁成 3:4</option><option value="blur" ${o.fit === "blur" ? "selected" : ""}>完整显示+虚化补边</option></select></label>
    <button class="btn gray sm" id="lvoff">清空</button><button class="btn" id="lvgo">做成 Live 图</button></div>` : "";
  if (!n) return;
  $("#lvbar").onchange = e => {
    const k = e.target.dataset.o; if (!k) return;
    if (k === "effect") {
      const [md, mk] = e.target.value.split(":"); LV.opts.mode = md; if (mk) LV.opts.motion = mk;
      try { localStorage.lvOpts = JSON.stringify(LV.opts); } catch { }
      return lvBar();
    }
    LV.opts[k] = k === "fit" ? e.target.value : +e.target.value;
    try { localStorage.lvOpts = JSON.stringify(LV.opts); } catch { }
  };
  $("#lvoff").onclick = () => { LV.sel.clear(); lvGrid(); lvBar(); };
  $("#lvgo").onclick = async () => {
    const btn = $("#lvgo"); btn.disabled = true;
    try {
      const r = await api("/api/live/jobs", "POST", { items: [...LV.sel.values()], opts: LV.opts });
      toast(r.created ? `已提交 ${r.created} 个任务${r.skipped ? `,${r.skipped} 个已存在` : ""}` : `这 ${r.skipped} 张已经有同样参数的任务了`);
      LV.sel.clear(); lvGrid(); lvTab("jobs"); await lvRefresh(); window.scrollTo(0, 0);
    } catch (e) { toast(e.message, 1); btn.disabled = false; }
  };
}

// ---------- 状态条 + 任务列表(每 3 秒刷新) ----------
async function lvRefresh() {
  clearTimeout(LV.timer);
  if (state.tab !== "live" || !$("#lvjobs")) return;
  try { [LV.st, LV.jobs] = await Promise.all([api("/api/live/status"), api("/api/live/jobs")]); } catch (e) { if (!LV.st) { $("#lvst").innerHTML = `<div class="banner err">${esc(e.message)}</div>`; } }
  if (state.tab !== "live" || !$("#lvjobs")) return;
  lvStatus(); lvJobs();
  LV.timer = setTimeout(lvRefresh, 3000);
}

const lvAgo = s => s < 90 ? `${s} 秒前` : s < 5400 ? `${Math.round(s / 60)} 分钟前` : s < 172800 ? `${Math.round(s / 3600)} 小时前` : `${Math.round(s / 86400)} 天前`;
function lvLine(label, w, onl, extra) {
  const x = w.workers[0];
  return `<div class="banner ${onl ? "ok" : "warn"} lv-st"><div><b>${label}:${onl ? `已连接 · ${esc(x.worker)}` : x ? `已离线(${esc(x.worker)} 最后一次在 ${lvAgo(x.ago)})` : "还没有连接过"}</b>
    <span class="sub" style="color:inherit;margin-left:8px">${extra}</span></div></div>`;
}
function lvStatus() {
  const s = LV.st; if (!s) return;
  const v = s.video || { workers: [], online: false, pending: 0, running: 0 };
  const open = $("#lvhow")?.open ?? !(s.workers[0] || v.workers[0]);
  const base = location.origin;
  $("#lvst").innerHTML = lvLine("Mac(做实况)", s, s.online, `排队 ${s.pending} · 制作中 ${s.running} · 已完成 ${s.done}${s.error ? ` · 失败 ${s.error}` : ""}`)
    + lvLine("视频生成机(真实动作)", v, v.online, `排队 ${v.pending} · 生成中 ${v.running}`)
    + `<details class="lv-how" id="lvhow" ${open ? "open" : ""}><summary>两台机器怎么启动</summary>
      <p class="sub">任务可以先提交,会一直排队,等对应的程序启动后自动开始做。<b>Token</b> 在管理端「接口配置」里复制采集端 Token。</p>
      <ol><li><b>视频生成机</b>(装了 ComfyUI + MiniMax H3 的电脑,只有「真实动作」需要),先启动 ComfyUI,再在 <code>tools/livephoto</code> 里运行:<pre>python video_worker.py --server ${esc(base)} --token 你的Token</pre></li>
      <li><b>Mac</b>:把 <code>tools/livephoto</code> 拷到 MacBook,装一次依赖 <code>pip3 install -r requirements-mac.txt</code>,再运行:<pre>python3 mac_worker.py --server ${esc(base)} --token 你的Token</pre></li>
      <li>做好的 <code>.pvt</code> 包存在 Mac 的 <code>~/LivePhotos</code>,同时回传到这里。双击 <code>.pvt</code> 导入「照片」,再 AirDrop 到 iPhone;或在「任务」里「下载」后解压。</li></ol>
      <p class="sub">流程:真实动作 = 先在视频生成机上由本机显卡生成视频 → 再由 Mac 做成实况;慢推镜头 = 只需要 Mac。Windows 上没法做实况:必须由 macOS 写入系统识别的配对信息。</p></details>`;
}

function lvJobs() {
  const js = LV.jobs, act = js.filter(j => j.status === "pending" || j.status === "running").length;
  $("#lvsum").textContent = js.length ? `共 ${js.length} 个${act ? `,${act} 个进行中` : ""}` : "";
  const n = $("#lvn"); if (n) { n.textContent = act || js.length || ""; n.className = act ? "run" : ""; n.title = act ? "进行中的任务数" : "任务总数"; }
  $("#lvjobs").innerHTML = js.length ? `<div class="lv-jobs">${js.map(j => {
    const [lab, cls] = lvState(j), o = j.opts || {}, motion = j.mode === "motion";
    const how = motion ? `真实动作 · ${esc(j.motion || lvMotionLabel(o.motion))} · ${o.seconds || 3} 秒` : `慢推镜头 · 推近 ${Math.round(((o.zoom || 1) - 1) * 100)}% · ${o.seconds || 5} 秒`;
    return `<article class="lv-job" data-j="${j.id}"><img src="/images/${esc(j.image)}" alt="" loading="lazy">
      <div class="lv-jt"><b>${esc(j.title)}</b><span class="sub">${how} · ${esc(LV_FIT[o.fit] || "")} · ${esc(when(j.created))}${j.worker ? ` · ${esc(j.worker)}` : ""}${j.size ? ` · ${(j.size / 1048576).toFixed(1)} MB` : ""}</span>
        ${j.status === "error" ? `<span class="fl-err">${esc(j.error)}</span>` : ""}</div>
      <span class="fl-pill ${cls}">${lab}</span>
      <div class="lv-ja">${j.has_video ? `<button class="btn gray sm" data-a="video" title="生成的视频(还没做成实况)">看视频</button>` : ""}
        ${j.has_result ? `<button class="btn gray sm" data-a="play">预览实况</button><a class="btn gray sm" href="/api/live/jobs/${j.id}/download">下载</a>` : ""}
        ${j.status === "error" || j.status === "done" ? `<button class="btn ghost sm" data-a="retry" title="${motion && j.has_video ? "视频保留,只重做实况" : "按原参数重新生成"}">${j.status === "error" ? "重试" : "重做"}</button>` : ""}
        ${motion && j.has_video && j.status !== "running" ? `<button class="btn ghost sm" data-a="regen" title="丢掉这条视频,重新生成一条">重拍视频</button>` : ""}
        ${j.status !== "running" ? `<button class="btn ghost sm danger" data-a="del" aria-label="删除">删除</button>` : ""}</div></article>`;
  }).join("")}</div>` : `<div class="empty"><b>还没有 Live 图任务</b>在「选图」里点选图片,再点底部的「做成 Live 图」</div>`;
}

async function lvJobClick(e) {
  const b = e.target.closest("[data-a]"); if (!b) return;
  const id = +b.closest("[data-j]").dataset.j, a = b.dataset.a;
  try {
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

window.FLOW_VIEWS.live = { t: "Live 图", d: "把笔记配图做成 iPhone 实况照片:真实动作(本机显卡生成视频)或慢推镜头,最后由 MacBook 做成实况", fn: fLive };
