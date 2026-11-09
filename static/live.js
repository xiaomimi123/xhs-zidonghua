// ---------- Live 图:从成品库选图 → 排队 → Mac 端生成 → 下载 .pvt 包 AirDrop 到 iPhone ----------
// 服务端只管队列和成品文件;动画渲染和 .pvt 打包在 MacBook 上跑(tools/livephoto/mac_worker.py)。
const LV = { lib: [], runs: [], pid: 0, todo: false, show: 60, sel: new Map(), jobs: [], st: null, timer: 0,
  opts: Object.assign({ seconds: 5, zoom: 1.08, focus_y: 0.35, fit: "crop" }, (() => { try { return JSON.parse(localStorage.lvOpts || "{}"); } catch { return {}; } })()) };
const LV_STATUS = { pending: ["排队中", "warn"], running: ["生成中", "run"], done: ["已完成", "ok"], error: ["失败", "bad"] };
const LV_FIT = { crop: "裁成 3:4", blur: "完整+虚化补边" };
const lvKey = (d, img) => `${d}:${img}`;

async function fLive() {
  clearTimeout(LV.timer);
  [LV.lib, LV.runs] = await Promise.all([api("/api/library"), api("/api/runs").catch(() => [])]);
  LV.lib = LV.lib.filter(d => d.images.length);
  $("#main").innerHTML = `<div id="lvst"></div>
    <div class="lv-h"><h3>① 选图</h3><span class="sub">点图片选中,一张图 = 一个 Live 图;封面最常用</span>
      <span class="lv-f"><button class="chip ${LV.todo ? "on" : ""}" id="lvtodo">只看未发布</button>
      <select id="lvrun" aria-label="按任务筛选"><option value="0">全部任务</option>${LV.runs.map(r => `<option value="${r.id}" ${LV.pid === r.id ? "selected" : ""}>${esc(r.name)}</option>`).join("")}</select></span></div>
    <div id="lvgrid"></div>
    <div class="lv-h"><h3>② 任务</h3><span class="sub" id="lvsum"></span><span class="lv-f"><button class="btn gray sm" id="lvclear">清除已完成</button></span></div>
    <div id="lvjobs"></div><div id="lvbar"></div>`;
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
  lvGrid(); lvBar(); lvRefresh();
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
  const n = LV.sel.size, o = LV.opts;
  document.body.classList.toggle("fl-picking", n > 0);
  const opt = (v, cur, label) => `<option value="${v}" ${+cur === v ? "selected" : ""}>${label}</option>`;
  $("#lvbar").innerHTML = n ? `<div class="fl-pickbar lv-bar" role="toolbar" aria-label="Live 图设置"><span>已选 <b>${n}</b> 张</span>
    <label class="lv-o">推近 <select data-o="zoom">${opt(1.05, o.zoom, "轻微")}${opt(1.08, o.zoom, "标准")}${opt(1.15, o.zoom, "明显")}</select></label>
    <label class="lv-o">焦点 <select data-o="focus_y">${opt(0.3, o.focus_y, "偏上")}${opt(0.35, o.focus_y, "略偏上")}${opt(0.5, o.focus_y, "居中")}${opt(0.7, o.focus_y, "偏下")}</select></label>
    <label class="lv-o">时长 <select data-o="seconds">${opt(3, o.seconds, "3 秒")}${opt(5, o.seconds, "5 秒")}</select></label>
    <label class="lv-o" title="图片不是 3:4 时怎么处理">非 3:4 的图 <select data-o="fit"><option value="crop" ${o.fit === "crop" ? "selected" : ""}>裁成 3:4</option><option value="blur" ${o.fit === "blur" ? "selected" : ""}>完整显示+虚化补边</option></select></label>
    <button class="btn gray sm" id="lvoff">清空</button><button class="btn" id="lvgo">做成 Live 图</button></div>` : "";
  if (!n) return;
  $("#lvbar").onchange = e => {
    const k = e.target.dataset.o; if (!k) return;
    LV.opts[k] = k === "fit" ? e.target.value : +e.target.value;
    try { localStorage.lvOpts = JSON.stringify(LV.opts); } catch { }
  };
  $("#lvoff").onclick = () => { LV.sel.clear(); lvGrid(); lvBar(); };
  $("#lvgo").onclick = async () => {
    const btn = $("#lvgo"); btn.disabled = true;
    try {
      const r = await api("/api/live/jobs", "POST", { items: [...LV.sel.values()], opts: LV.opts });
      toast(r.created ? `已提交 ${r.created} 个任务${r.skipped ? `,${r.skipped} 个已存在` : ""}` : `这 ${r.skipped} 张已经有同样参数的任务了`);
      LV.sel.clear(); lvGrid(); lvBar(); await lvRefresh(); $("#lvjobs").scrollIntoView({ behavior: "smooth", block: "start" });
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

function lvStatus() {
  const s = LV.st; if (!s) return;
  const w = s.workers[0], open = $("#lvhow")?.open ?? !w;
  const base = location.origin;
  $("#lvst").innerHTML = `<div class="banner ${s.online ? "ok" : "warn"} lv-st"><div><b>${s.online ? `Mac 已连接:${esc(w.worker)}` : w ? `Mac 已离线(${esc(w.worker)} 最后一次在 ${lvAgo(w.ago)})` : "还没有 Mac 连接过"}</b>
      <span class="sub" style="color:inherit;margin-left:8px">排队 ${s.pending} · 生成中 ${s.running} · 已完成 ${s.done}${s.error ? ` · 失败 ${s.error}` : ""}</span>
      ${s.online ? "" : `<div class="sub" style="color:inherit">任务可以先提交,会一直排队,等 Mac 上的程序启动后自动开始做。</div>`}</div></div>
    <details class="lv-how" id="lvhow" ${open ? "open" : ""}><summary>Mac 上怎么启动</summary>
      <ol><li>把项目里的 <code>tools/livephoto</code> 文件夹拷到 MacBook,终端进到这个文件夹,装一次依赖:<pre>pip3 install -r requirements-mac.txt</pre></li>
      <li>启动(保持窗口开着,它会自动领任务;<b>Token</b> 在管理端「接口配置」里复制采集端 Token):<pre>python3 mac_worker.py --server ${esc(base)} --token 你的Token</pre></li>
      <li>做好的 <code>.pvt</code> 包会存在 Mac 的 <code>~/LivePhotos</code>,同时回传到这里。双击 <code>.pvt</code> 导入「照片」,再 AirDrop 到 iPhone;或在下面「下载」后解压。</li></ol>
      <p class="sub">Windows 上没法生成:实况照片必须由 macOS 写入系统识别的配对信息。</p></details>`;
}
const lvAgo = s => s < 90 ? `${s} 秒前` : s < 5400 ? `${Math.round(s / 60)} 分钟前` : s < 172800 ? `${Math.round(s / 3600)} 小时前` : `${Math.round(s / 86400)} 天前`;

function lvJobs() {
  const js = LV.jobs, act = js.filter(j => j.status === "pending" || j.status === "running").length;
  $("#lvsum").textContent = js.length ? `共 ${js.length} 个${act ? `,${act} 个进行中` : ""}` : "";
  $("#lvjobs").innerHTML = js.length ? `<div class="lv-jobs">${js.map(j => {
    const [lab, cls] = LV_STATUS[j.status] || [j.status, ""], o = j.opts || {};
    return `<article class="lv-job" data-j="${j.id}"><img src="/images/${esc(j.image)}" alt="" loading="lazy">
      <div class="lv-jt"><b>${esc(j.title)}</b><span class="sub">${esc(LV_FIT[o.fit] || "")} · 推近 ${Math.round(((o.zoom || 1) - 1) * 100)}% · ${o.seconds || 5} 秒 · ${esc(when(j.created))}${j.worker ? ` · ${esc(j.worker)}` : ""}${j.size ? ` · ${(j.size / 1048576).toFixed(1)} MB` : ""}</span>
        ${j.status === "error" ? `<span class="fl-err">${esc(j.error)}</span>` : ""}</div>
      <span class="fl-pill ${cls}">${lab}</span>
      <div class="lv-ja">${j.has_result ? `<button class="btn gray sm" data-a="play">预览</button><a class="btn gray sm" href="/api/live/jobs/${j.id}/download">下载</a>` : ""}
        ${j.status === "error" || j.status === "done" ? `<button class="btn ghost sm" data-a="retry" title="按原参数重新生成">${j.status === "error" ? "重试" : "重做"}</button>` : ""}
        ${j.status !== "running" ? `<button class="btn ghost sm danger" data-a="del" aria-label="删除">删除</button>` : ""}</div></article>`;
  }).join("")}</div>` : `<div class="empty"><b>还没有 Live 图任务</b>在上面点选图片,再点底部的「做成 Live 图」</div>`;
}

async function lvJobClick(e) {
  const b = e.target.closest("[data-a]"); if (!b) return;
  const id = +b.closest("[data-j]").dataset.j, a = b.dataset.a;
  try {
    if (a === "play") return lvPlay(id);
    if (a === "del") { if (!confirm("删除这个任务和它的成品?")) return; await api(`/api/live/jobs/${id}`, "DELETE"); }
    if (a === "retry") await api(`/api/live/jobs/${id}/retry`, "POST");
  } catch (err) { toast(err.message, 1); }
  lvRefresh();
}

function lvPlay(id) {
  const m = document.createElement("div"); m.className = "lb";
  const close = () => { m.remove(); document.removeEventListener("keydown", key); };
  const key = e => { if (e.key === "Escape") close(); };
  m.innerHTML = `<video src="/api/live/jobs/${id}/preview" autoplay loop muted playsinline controls></video><button class="lb-c" data-c aria-label="关闭">✕</button>`;
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

window.FLOW_VIEWS.live = { t: "Live 图", d: "把笔记配图做成 iPhone 实况照片(缓慢推近的动态封面),由 MacBook 生成", fn: fLive };
