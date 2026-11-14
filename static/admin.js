// 服务器时间(UTC,形如 2026-11-03 07:02:00)→ 浏览器本地时间。kind: "md" 月-日 时:分(默认)/ "ymd" 年-月-日 / "d" 月-日
const lt = (s, kind = "md") => {
  const d = new Date(String(s || "").replace(" ", "T") + "Z"); if (!s || isNaN(d)) return String(s || "").slice(kind === "ymd" ? 0 : 5, kind === "ymd" ? 10 : 16);
  const p = n => String(n).padStart(2, "0"), ymd = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  return kind === "ymd" ? ymd : kind === "d" ? ymd.slice(5) : `${ymd.slice(5)} ${p(d.getHours())}:${p(d.getMinutes())}`;
};
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = async (url, method = "GET", body, raw) => {
  const r = await fetch(url, { method, headers: raw ? {} : { "Content-Type": "application/json" }, body: raw ?? (body ? JSON.stringify(body) : undefined) });
  if (r.status === 401) { gate(); throw new Error("需要管理员登录"); }
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m } catch { } throw new Error(typeof m === "string" ? m : JSON.stringify(m)); }
  return r.json();
};
const toast = (msg, err) => {
  $$(".toast").forEach(t => t.remove());
  const t = document.createElement("div"); t.className = "toast" + (err ? " err" : ""); t.textContent = msg;
  document.body.appendChild(t); setTimeout(() => t.remove(), 2600);
};
const fmt = n => n >= 10000 ? (n / 10000).toFixed(1) + "w" : (n ?? 0);
const ago = s => s == null ? "从未连接" : s < 60 ? `${s} 秒前` : s < 3600 ? `${Math.round(s / 60)} 分钟前` : `${Math.round(s / 3600)} 小时前`;
const TST = { pending: ["等待领取", ""], running: ["采集中", "run"], done: ["已完成", "done"], failed: ["失败", "error"] };
const st = s => `<span class="st ${(TST[s] || [])[1] ?? ""}">${(TST[s] || [s])[0]}</span>`;

const VIEWS = [
  ["overview", "◎", "概览", "系统状态与待办"],
  ["tasks", "⇅", "采集监控", "采集端在线状态与全部采集任务"],
  ["notes", "▤", "笔记库", "全部采集到的笔记,支持搜索、删除和批量导入"],
  ["qianfan", "▦", "数据看板", "千帆后台的账号数据、笔记表现、热门笔记 / 话题 / 关键词排行"],
  ["tracks", "◈", "赛道配置", "用户端可选的赛道、推荐关键词,以及拆解和复刻时使用的赛道知识库"],
  ["rules", "⚑", "合规词库", "复刻成稿后自动扫描的违规词;被限流后把真实命中的词补进来并写明日期"],
  ["settings", "⚙", "接口配置", "DeepSeek、生图模型、并发与采集端对接"],
  ["staff", "☺", "人员管理", "员工账号:前端登录、停用、重置密码、使用情况"],
  ["security", "⚿", "账号安全", "管理员密码"],
];
const state = { tab: VIEWS.some(v => v[0] === location.hash.slice(1)) ? location.hash.slice(1) : "overview", tf: "", tq: "", np: 0, nq: "", nproj: 0, nsel: new Set() };
let pollT;
const loop = (fn, ms) => {
  const tab = state.tab;
  const tick = async () => { if (state.tab !== tab) return; try { await fn(); } catch { } if (state.tab === tab) pollT = setTimeout(tick, ms); };
  clearTimeout(pollT); pollT = setTimeout(tick, ms);
};

function nav() {
  $("#nav").innerHTML = VIEWS.map(([k, ic, n]) => `<button class="${k === state.tab ? "on" : ""}" data-k="${k}"><i>${ic}</i><span>${n}</span></button>`).join("");
  const v = VIEWS.find(x => x[0] === state.tab);
  $("#pt").textContent = v[2]; $("#pd").textContent = v[3]; $("#pa").innerHTML = "";
  document.title = v[2] + " · 管理端";
}
const go = k => { state.tab = k; location.hash = k; render(); window.scrollTo(0, 0); };
document.addEventListener("click", e => {
  const b = e.target.closest("#nav button"); if (b) return go(b.dataset.k);
  const g = e.target.closest("[data-go]"); if (g) { e.preventDefault(); go(g.dataset.go); }
});
$("#logout").onclick = async () => { await fetch("/api/admin/logout", { method: "POST" }); location.reload(); };
function render() {
  clearTimeout(pollT); nav();
  ({ overview: vOverview, tasks: vTasks, notes: vNotes, qianfan: vQianfan, tracks: vTracks, rules: vRules, settings: vSettings, staff: vStaff, security: vSecurity })[state.tab]();
}

// ---------- 概览 ----------
async function vOverview() {
  const [o, s] = await Promise.all([api("/api/admin/overview"), api("/api/admin/settings")]);
  const sum = obj => Object.values(obj).reduce((a, b) => a + b, 0);
  const c = o.collector;
  const checks = [
    [!o.init_pw_file, "修改初始管理员密码", "security"],
    [!!s.deepseek_api_key, "配置 DeepSeek API Key", "settings"],
    [!!(s.image_base_url && s.image_api_key), "配置生图接口", "settings"],
    [c.online, c.online ? `采集端已上线(${c.workers.filter(w => w.ago < (c.online_sec || 150)).length} 台)` : "采集端上线", "tasks"],
    [s.staff_count > 0 || s.user_password_set, "创建员工账号(前端需要登录才能使用)", "staff"],
  ];
  $("#main").innerHTML = `${o.init_pw_file ? `<div class="banner err">⚠ 还在使用自动生成的初始密码,请尽快到「账号安全」修改。</div>` : ""}
  <div class="stats">
    <div class="card stat"><span class="sub">采集端</span><b style="color:${c.online ? "var(--ok)" : "var(--faint)"}">${c.online ? "在线" : "离线"}</b><div class="mini">最近心跳 ${ago(c.ago)}</div></div>
    <div class="card stat"><span class="sub">采集任务</span><b>${sum(o.tasks)}</b><div class="mini"><span>等待 <em>${o.tasks.pending || 0}</em></span><span>进行中 <em>${o.tasks.running || 0}</em></span><span>失败 <em style="color:var(--err)">${o.tasks.failed || 0}</em></span></div></div>
    <div class="card stat"><span class="sub">笔记库</span><b>${o.notes}</b><div class="mini"><span>爆款 <em>${o.tiers.viral || 0}</em></span><span>热门 <em>${o.tiers.hot || 0}</em></span><span>待打标签 <em>${(o.ai.pending || 0) + (o.ai.running || 0)}</em></span><span>图片 <em>${o.mirror_mb} MB</em></span></div></div>
    <div class="card stat"><span class="sub">复刻草稿</span><b>${sum(o.drafts)}</b><div class="mini"><span>就绪 <em>${(o.drafts.ready || 0) + (o.drafts.text_done || 0)}</em></span><span>已导出 <em>${o.drafts.exported || 0}</em></span><span>出错 <em style="color:var(--err)">${o.drafts.error || 0}</em></span></div></div>
  </div>
  <section class="card"><div class="card-h"><b>上线检查</b></div><div class="checks">
    ${checks.map(([ok, t, k]) => `<div><span class="st ${ok ? "done" : "error"}">${ok ? "完成" : "待处理"}</span>${t}${ok ? "" : `<a data-go="${k}">去处理 →</a>`}</div>`).join("")}
  </div></section>`;
}

// ---------- 采集监控 ----------
async function vTasks() {
  $("#pa").innerHTML = `<button class="btn gray" id="rf">重试全部失败</button>`;
  $("#main").innerHTML = `<section class="card" style="margin-bottom:16px"><div class="card-h"><b>采集端</b><span class="sub">采集端每次领取任务即视为一次心跳,45 秒内有心跳算在线</span></div><div id="cols"></div></section>
  <section class="card lib">
    <div class="lib-h"><div><b>笔记库沉淀任务</b><p class="sub">采集端空闲时才会领取,用户的任务永远排在前面。抓到的笔记只进笔记库,以后做内容时直接复用。</p></div>
      <label class="lib-sw"><input type="checkbox" id="lon"><span>采集端空闲时自动抓取</span></label></div>
    <div class="lib-stat" id="lstat"></div>
    <div class="lib-step"><div class="lib-sh"><span class="no">1</span><b>选择关键词来源</b></div>
      <div class="seg" id="lsrc" role="tablist"><button class="on" data-src="manual">手动输入</button><button data-src="ai">AI 生成</button><button data-src="track">赛道预设词</button><button data-src="projects">用户做过的词</button></div>
      <div class="lib-src" id="lsrcbox"></div></div>
    <div class="lib-step"><div class="lib-sh"><span class="no">2</span><b>确认关键词并添加</b><span class="sub" id="lcount"></span></div>
      <textarea id="lq" rows="5" placeholder="每行一个关键词,例如:&#10;金汤酱料做法&#10;酸菜鱼做法&#10;复合调味料推荐"></textarea>
      <div class="lib-opts">
        <label><span>任务类型</span><select id="lt"><option value="keyword">关键词</option><option value="title">标题</option><option value="author">博主</option></select></label>
        <label><span>赛道标签(可选)</span><select id="ll"><option value="">不指定,由 AI 判断</option></select></label>
        <label><span>每个任务条数</span><input id="llim" type="number" min="1" max="100"></label>
        <button class="btn" id="ladd">添加到采集队列</button></div>
      <div class="lib-msg" id="lmsg" role="status" hidden></div></div>
    <div class="lib-foot"><span class="sub">已经在队列里或最近 7 天采过的词会自动跳过。</span><button class="btn ghost sm danger" id="lclr">清空排队中的沉淀任务</button></div>
  </section>
  <div class="tbar"><div class="seg" id="seg"></div><div class="seg" id="seg2"></div><div class="seg" id="seg3" role="group" aria-label="按发起人筛选"></div><input class="grow" id="tq" placeholder="搜索关键词 / 赛道" value="${esc(state.tq)}"></div>
  <section class="card flush"><div class="tbl-wrap"><table class="tbl"><thead><tr><th>#</th><th>来源</th><th>发起人</th><th>类型</th><th>搜索内容</th><th>状态</th><th class="num">笔记</th><th>采集端</th><th>时间</th><th></th></tr></thead><tbody id="tb"></tbody></table></div></section>`;
  let drawSeq = 0;
  const draw = async () => {
    const my = ++drawSeq;  // 每 3 秒的自动刷新可能在删除之前就发出、删除之后才回来:只认最后发起的那一次,旧结果丢掉,免得把已删的任务又画回来
    const [o, ts, ls, who] = await Promise.all([api("/api/admin/overview"), api(`/api/admin/tasks?status=${state.tf}&q=${encodeURIComponent(state.tq)}&purpose=${state.tp || ""}&by=${encodeURIComponent(state.tw || "")}`), api("/api/admin/library_stats"), api("/api/admin/tasks_who")]);
    if (my !== drawSeq) return;
    if (document.activeElement !== $("#lon")) $("#lon").checked = ls.enabled;
    $("#lstat").textContent = `沉淀任务:排队 ${ls.pending} · 进行中 ${ls.running} · 已完成 ${ls.done}${ls.failed ? ` · 失败 ${ls.failed}` : ""};累计为笔记库新增 ${ls.new_total} 篇(近 7 天 ${ls.new_7d} 篇)${ls.enabled ? "" : " · 已暂停,采集端不会领取"}`;
    const names = Object.keys(who || {});
    if (state.tw && !names.includes(state.tw)) state.tw = "";
    $("#seg3").innerHTML = names.length > 1 ? [["", "全部发起人"], ...names.map(n => [n, n])].map(([k, n]) => `<button class="${(state.tw || "") === k ? "on" : ""}" data-w="${esc(k)}">${esc(n)}${k ? `<em>${who[k]}</em>` : ""}</button>`).join("") : "";
    $("#seg2").innerHTML = [["", "全部来源"], ["project", "用户任务"], ["library", "沉淀任务"]].map(([k, n]) => `<button class="${(state.tp || "") === k ? "on" : ""}" data-p="${k}">${n}</button>`).join("");
    const ws = o.collector.workers;
    $("#cols").innerHTML = ws.length ? `<table class="tbl"><thead><tr><th>名称</th><th>IP</th><th>状态</th><th>最近心跳</th><th class="num">累计领取任务</th><th>首次连接</th><th title="采集端是否在用 POST /api/notes/known 跳过已有笔记">跳过已有笔记</th></tr></thead><tbody>${ws.map(w => `<tr><td><b>${esc(w.worker)}</b></td><td class="mono">${esc(w.ip)}</td>
      <td>${w.ago < 150 ? `<span class="st done">在线</span>` : `<span class="st">离线</span>`}</td><td class="sub">${ago(w.ago)}</td><td class="num">${w.claimed}</td><td class="sub">${esc(lt(w.first_seen))}</td>
      <td>${w.known_calls ? `<span class="st done">已使用</span><div class="sub">${w.known_calls} 次 · 查过 ${w.known_total} 篇,其中 ${w.known_hit} 篇已有 · ${ago(w.known_ago)}</div>` : `<span class="st">未使用</span><div class="sub">还没调用过「先问哪些已有」</div>`}</td></tr>`).join("")}</tbody></table>`
      : `<div class="sub">还没有采集端连接过。对接方式见 <a data-go="settings" href="#">接口配置 → 采集端对接</a>。</div>`;
    const cnt = o.tasks, all = Object.values(cnt).reduce((a, b) => a + b, 0);
    $("#seg").innerHTML = [["", "全部", all], ["pending", "等待", cnt.pending], ["running", "采集中", cnt.running], ["done", "完成", cnt.done], ["failed", "失败", cnt.failed]]
      .map(([k, n, c]) => `<button class="${state.tf === k ? "on" : ""}" data-f="${k}">${n}<em>${c || 0}</em></button>`).join("");
    $("#tb").innerHTML = ts.map(t => `<tr><td class="sub">${t.id}</td><td class="nowrap">${t.purpose === "library" ? `<span class="tier normal">沉淀</span> ${esc(t.track || "")}` : `${esc(t.track || "-")}<div class="sub">${t.project_id ? "项目 #" + t.project_id : t.purpose === "qianfan" ? "千帆同步" : ""}</div>`}</td>
      <td class="nowrap">${esc(t.by || "-")}</td><td class="nowrap">${{ title: "标题", author: "博主" }[t.type] || "关键词"}</td><td><b>${esc(t.query)}</b></td>
      <td>${st(t.status)}${t.error ? `<div class="err" title="${esc(t.error)}">${esc(t.error)}</div>` : ""}</td>
      <td class="num">${t.purpose === "library" ? `新增 ${t.new_count} / 共 ${t.note_count}` : `${t.note_count} / ${t.lim}`}</td><td class="sub">${esc(t.worker || "-")}</td>
      <td class="sub nowrap">${esc(lt(t.finished_at || t.claimed_at || t.created))}</td>
      <td class="nowrap"><button class="btn ghost sm" data-retry="${t.id}">${t.status === "pending" ? "" : "重新下发"}</button><button class="btn ghost sm danger" data-del="${t.id}">${state.delT === t.id ? "再点确认删除" : "删除"}</button></td></tr>`).join("")
      || `<tr><td colspan="10" class="sub" style="text-align:center;padding:40px">没有任务</td></tr>`;
    $$("#tb [data-retry]").forEach(b => { if (!b.textContent) b.remove(); });
  };
  const tracks = await api("/api/admin/tracks");
  $("#ll").innerHTML += tracks.map(t => `<option>${esc(t.name)}</option>`).join("");
  $("#llim").placeholder = `默认 ${(await api("/api/admin/settings")).collect_limit}`;
  // 提示条:操作结果显示在表单里(不只是角落里一闪而过的提示),出错时写明原因和怎么办
  const msg = (text, kind = "info") => { const m = $("#lmsg"); m.hidden = !text; m.className = "lib-msg " + kind; m.textContent = text || ""; };
  const words = () => $("#lq").value.split("\n").map(s => s.trim()).filter(Boolean);
  const count = () => { const n = words().length; $("#lcount").textContent = n ? `${n} 行` : ""; };
  const fill = list => {   // 把候选词放进输入框(和已有的合并去重),等管理员确认
    $("#lq").value = [...new Set([...words(), ...list])].join("\n"); count(); $("#lt").value = "keyword";
  };
  const busy = async (btn, label, fn) => {
    const old = btn.textContent, t0 = Date.now(); btn.disabled = true; btn.innerHTML = `<span class="fl-spin" aria-hidden="true"></span>${label}`; msg("");
    // 模型接口有时很慢(几十秒甚至更久):显示已经等了多久,别让人以为没反应
    const tick = setInterval(() => { const n = Math.round((Date.now() - t0) / 1000); if (n >= 3) msg(`${label.replace("…", "")} 已等待 ${n} 秒${n >= 15 ? "(模型接口目前响应较慢,最长可能要 1~2 分钟,请不要关闭页面)" : ""}`, "info"); }, 1000);
    try { await fn(); } catch (e) { msg("操作失败:" + (/503|busy/i.test(e.message) ? "DeepSeek 暂时繁忙(对方服务的问题),稍等一会儿再点一次。" : e.message), "err"); }
    clearInterval(tick); btn.disabled = false; btn.textContent = old;
  };
  const SRC = {
    manual: () => `<p class="sub">在下面第 2 步的输入框里直接填,每行一个(用逗号分隔也行)。</p>`,
    ai: () => `<div class="lib-ai"><label><span>产品或品类</span><input id="lex" placeholder="例如:金汤酱料、红腐乳、防晒霜"></label>
      <div class="lib-btns"><button class="btn gray" id="lexgo">生成核心词</button><button class="btn gray" id="lexadj">生成相邻词</button></div></div>
      <p class="sub"><b>核心词</b>:产品本身的各种说法(做法、怎么选、测评……)。<b>相邻词</b>:上位品类、常用菜、同类替代品,品类本身笔记很少时用。生成大约要 10 秒。</p>`,
    track: () => `<div class="lib-ai"><label><span>赛道</span><select id="ltr">${tracks.map(t => `<option>${esc(t.name)}</option>`).join("")}</select></label>
      <div class="lib-btns"><button class="btn gray" id="ltrgo">载入这个赛道的预设词</button></div></div>`,
    projects: () => `<div class="lib-btns"><button class="btn gray" id="lpj">载入近 90 天用户做过的关键词</button></div><p class="sub">这些词有真实需求,值得多沉淀一些笔记。</p>`,
  };
  const showSrc = k => {
    $$("#lsrc button").forEach(b => b.classList.toggle("on", b.dataset.src === k)); $("#lsrcbox").innerHTML = SRC[k](); msg("");
    const topic = () => $("#lex").value.trim();
    const expand = (adjacent, id, label) => busy($("#" + id), "生成中…", async () => {
      if (!topic()) return msg("先填一个产品或品类", "err");
      const r = await api("/api/admin/keywords/expand", "POST", { topic: topic(), track: $("#ll").value, n: adjacent ? 12 : 30, adjacent });
      fill(r.keywords); msg(r.keywords.length ? `已生成 ${r.keywords.length} 个${adjacent ? "相邻" : "核心"}关键词,放进了下面的输入框。删掉不要的,再点「添加到采集队列」。` : "没有生成新的词(可能都已经在队列里了)", r.keywords.length ? "ok" : "info");
    });
    if (k === "ai") { $("#lexgo").onclick = () => expand(false, "lexgo"); $("#lexadj").onclick = () => expand(true, "lexadj"); $("#lex").onkeydown = e => { if (e.key === "Enter") expand(false, "lexgo"); }; }
    if (k === "track") $("#ltrgo").onclick = () => busy($("#ltrgo"), "载入中…", async () => {
      const r = await api(`/api/admin/library_tasks/candidates?source=track&track=${encodeURIComponent($("#ltr").value)}`);
      fill(r.keywords); if (!$("#ll").value) $("#ll").value = $("#ltr").value;
      msg(r.keywords.length ? `已载入 ${r.keywords.length} 个预设词${r.skipped ? `(另有 ${r.skipped} 个已在队列或最近采过,已去掉)` : ""},赛道标签已选为「${$("#ltr").value}」。` : "这个赛道的预设词都已经在队列里或最近采过了", r.keywords.length ? "ok" : "info");
    });
    if (k === "projects") $("#lpj").onclick = () => busy($("#lpj"), "载入中…", async () => {
      const r = await api("/api/admin/library_tasks/candidates?source=projects");
      fill(r.keywords); msg(r.keywords.length ? `已载入 ${r.keywords.length} 个用户做过的关键词${r.skipped ? `(另有 ${r.skipped} 个已在队列或最近采过,已去掉)` : ""}。` : "用户做过的关键词都已经在队列里或最近采过了", r.keywords.length ? "ok" : "info");
    });
  };
  $("#lsrc").onclick = e => { const b = e.target.closest("[data-src]"); if (b) showSrc(b.dataset.src); };
  showSrc("manual");
  $("#lq").oninput = count;
  $("#lon").onchange = async e => { await api("/api/admin/library_collect", "POST", { enabled: e.target.checked }); toast(e.target.checked ? "已开启:采集端空闲时会抓取沉淀任务" : "已暂停:采集端不再领取沉淀任务"); draw(); };
  $("#ladd").onclick = () => busy($("#ladd"), "添加中…", async () => {
    const qs = words(); if (!qs.length) return msg("先填写或载入要采集的关键词", "err");
    const r = await api("/api/admin/library_tasks", "POST", { type: $("#lt").value, queries: qs, label: $("#ll").value, limit: +$("#llim").value || 0 });
    if (r.added) { $("#lq").value = ""; count(); }
    msg(r.added ? `已添加 ${r.added} 个任务到采集队列${r.skipped.length ? `,跳过 ${r.skipped.length} 个(已在队列或最近采过)` : ""}。采集端空闲时会自动领取。` : `没有新增:${r.skipped.length ? r.skipped.slice(0, 3).map(x => x.query).join("、") + " 等都已在队列中或最近采过" : "没有可添加的内容"}`, r.added ? "ok" : "info");
    draw();
  });
  $("#lclr").onclick = async () => { if (confirm("清空所有还在排队的沉淀任务?正在采集和已完成的不受影响")) { const r = await api("/api/admin/library_tasks/pending", "DELETE"); msg(`已清空 ${r.n} 个排队中的沉淀任务`, "ok"); draw(); } };
  await draw(); loop(draw, 3000);
  let deb; $("#tq").oninput = e => { clearTimeout(deb); deb = setTimeout(() => { state.tq = e.target.value.trim(); draw(); }, 300); };
  $("#main").onclick = async e => {
    const f = e.target.closest("[data-f]"); if (f) { state.tf = f.dataset.f; return draw(); }
    const pp = e.target.closest("[data-p]"); if (pp) { state.tp = pp.dataset.p; return draw(); }
    const pw = e.target.closest("[data-w]"); if (pw) { state.tw = pw.dataset.w; return draw(); }
    const t = e.target;
    try {
      if (t.dataset.retry) { await api(`/api/admin/tasks/${t.dataset.retry}/retry`, "POST"); return draw(); }
      if (t.dataset.del) {  // 不用浏览器自带的确认框(有的环境会拦掉),改成点两次;列表每 3 秒刷新,确认状态记在 state 里
        const id = +t.dataset.del;
        if (state.delT !== id) { state.delT = id; clearTimeout(state.delTm); state.delTm = setTimeout(() => { state.delT = 0; draw(); }, 5000); return draw(); }
        state.delT = 0; t.closest("tr")?.remove(); await api(`/api/admin/tasks/${id}`, "DELETE"); toast(`已删除任务 #${id}`); draw();
      }
    } catch (err) { toast(err.message, 1); draw(); }
  };
  $("#rf").onclick = async () => { const { n } = await api("/api/admin/tasks/retry_failed", "POST"); toast(`已重新下发 ${n} 个失败任务`); draw(); };
}

// ---------- 笔记库 ----------
// 在页面里查看一篇笔记:左边图片,右边标题、数据、正文、话题;可以上一篇/下一篇;需要时才跳到小红书
function viewNote(list, i) {
  const m = document.createElement("div"); m.className = "mask";
  const draw = () => {
    const n = list[i], pics = n.images && n.images.length ? n.images : (n.cover ? [n.cover] : []);
    m.innerHTML = `<div class="modal">${pics.length ? `<div class="l">${pics.map(u => `<img src="${esc(u)}" referrerpolicy="no-referrer" loading="lazy" alt="">`).join("")}</div>` : ""}
      <div class="r"><div class="row" style="gap:6px;margin:0 0 10px"><span class="sub">${i + 1} / ${list.length}</span><button class="btn gray sm" data-step="-1" aria-label="上一篇">‹</button><button class="btn gray sm" data-step="1" aria-label="下一篇">›</button><button class="btn ghost sm" data-x style="margin-left:auto">关闭</button></div>
        <h3 style="margin:0 0 6px;font-size:18px;line-height:1.5">${n.note_type === "video" ? `<span class="tp" style="margin-right:6px">视频</span>` : ""}${esc(n.title)}</h3>
        <div class="sub">${esc(n.author || "-")} · ♥ ${fmt(n.likes)} · ★ ${fmt(n.collects)} · 💬 ${fmt(n.comments)}${n.published_at ? " · 发布 " + esc(n.published_at) : ""} · ${tierB(n.tier)}</div>
        <div class="sub" style="margin-top:6px">${esc(n.track || "-")} · <b>${esc(n.topic || "待识别")}</b>${n.ai_type ? ` · ${esc(n.ai_type)}` : ""} · 搜索词:${esc(n.keyword || "-")}${n.dup_of ? ` · <span class="tier normal">重复</span>` : ""}${n.rel === "low" ? ` · <span style="color:var(--err)" title="${esc(n.rel_why)}">弱相关</span>` : ""}</div>
        ${n.ai_highlight ? `<div class="angle" style="margin-top:12px"><span>爆点</span>${esc(n.ai_highlight)}${n.ai_audience ? " · " + esc(n.ai_audience) : ""}</div>` : ""}
        <p style="white-space:pre-wrap;line-height:1.85;margin:14px 0">${esc(n.content) || "(没有正文)"}</p>
        ${n.tags?.length ? `<p class="sub">${n.tags.map(x => "#" + esc(x)).join(" ")}</p>` : ""}
        <div class="row" style="gap:8px;flex-wrap:wrap;margin-top:16px">
          <button class="btn gray sm" data-act="edit">编辑分类</button><button class="btn gray sm" data-act="raw">原始数据</button>
          ${n.url && n.url.startsWith("http") ? `<a class="btn gray sm" href="${esc(n.url)}" target="_blank" rel="noreferrer" style="text-decoration:none">在小红书打开 ↗</a>` : ""}</div></div></div>`;
  };
  const close = () => { m.remove(); document.removeEventListener("keydown", key); };
  const step = d => { i = (i + d + list.length) % list.length; draw(); m.querySelector(".r")?.scrollTo(0, 0); };
  const key = e => { if (e.key === "Escape") close(); else if (e.key === "ArrowRight") step(1); else if (e.key === "ArrowLeft") step(-1); };
  m.onclick = e => {
    if (e.target === m || e.target.closest("[data-x]")) return close();
    const s = e.target.closest("[data-step]"); if (s) return step(+s.dataset.step);
    const a = e.target.closest("[data-act]");
    if (a) { const id = list[i].id; close(); document.querySelector(`#tb [data-${a.dataset.act}="${id}"]`)?.click(); }
  };
  document.addEventListener("keydown", key); draw(); document.body.appendChild(m);
}
const PAGE = 50;
const TIER = { viral: "爆款", hot: "热门", normal: "普通" };
const AIS = { pending: "待打标签", running: "打标签中", done: "已打标签", error: "打标签失败" };
const tierB = t => `<span class="tier ${t}">${TIER[t] || t}</span>`;
const NF = { track: "", topic: "", keyword: "", ai_type: "", tier: "", ai_status: "", project_id: "0", q: "", sort: "new" };
async function vNotes() {
  const f = state.nf ||= { ...NF };
  const [fa, projects, tracks, fx] = await Promise.all([api(`/api/admin/notes/facets?track=${encodeURIComponent(f.track)}`), api("/api/admin/projects"), api("/api/admin/tracks"), api("/api/admin/notes/fields")]);
  const opt = (list, cur, label, name = k => k || "(空)") => `<option value="">${label}</option>` + list.map(x => `<option value="${esc(x.k)}" ${x.k === cur ? "selected" : ""}>${esc(name(x.k))}(${x.n})</option>`).join("");
  const trackNames = [...new Set([...tracks.map(t => t.name), ...fa.tracks.map(t => t.k).filter(Boolean)])];
  const aiN = Object.fromEntries(fa.ai.map(x => [x.k, x.n]));
  const mN = Object.fromEntries((fa.mirror || []).map(x => [x.k, x.n]));
  const filtered = Object.entries(f).some(([k, v]) => v !== NF[k]);
  $("#pa").innerHTML = `<button class="btn gray" id="tpc">整理主题</button><button class="btn gray" id="exp">导出备份</button><button class="btn gray" id="imp">导入笔记</button><button class="btn gray" id="retag" disabled>重新打标签</button><button class="btn" id="dels" disabled>删除选中</button>`;
  $("#main").innerHTML = `<section class="card import" id="impbox" hidden style="margin-bottom:16px">
      <div><div class="card-h"><b>导入笔记</b><span class="sub">支持 .json / .xlsx / .csv,字段与采集端回传标准相同</span></div>
        <label class="drop" id="drop"><input type="file" id="file" accept=".json,.xlsx,.csv" hidden><b>点击选择文件</b> 或拖到这里<br>
        <span>表头可用中文:标题、正文、作者、点赞、收藏、评论、链接、图片、话题、笔记ID、发布时间</span></label></div>
      <div class="impf">
        <div class="fld"><label>归入赛道 *</label><select id="itrack"><option value="">请选择</option>${trackNames.map(t => `<option ${t === f.track ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></div>
        <div class="fld"><label>关键词(可选)</label><input id="ikw" value="${esc(f.keyword)}"></div>
        <div class="fld"><label>同时加入项目(可选)</label><select id="iproj"><option value="0">不加入</option>${projects.map(p => `<option value="${p.id}">#${p.id} ${esc(p.track)} · ${esc(p.keywords.join("、"))}</option>`).join("")}</select></div>
        <button class="btn" id="ido">开始导入</button></div>
    </section>
    ${fa.no_track ? `<div class="banner warn">${fa.no_track} 篇笔记还没有赛道(笔记库沉淀任务没填赛道标签时会这样),系统会在后台自动判断并补上 · <a href="#" id="ftr">现在就补</a></div>` : ""}
    ${aiN.pending || aiN.running ? `<div class="banner warn">${(aiN.pending || 0) + (aiN.running || 0)} 篇等待 AI 打标签。没在动的话,检查「接口配置」里的 DeepSeek Key 和自动打标签开关。</div>` : ""}
    ${aiN.error ? `<div class="banner err">${aiN.error} 篇打标签失败 · <a href="#" id="rtf">全部重试</a></div>` : ""}
    ${fx.fields.length ? `<div class="banner" style="background:var(--soft);color:var(--sub)">采集端还传了这些工作台暂未使用的字段,已完整保存在每篇笔记的「原始数据」里:${fx.fields.slice(0, 10).map(x => `<b style="color:var(--ink)">${esc(x.k)}</b> ×${x.n}`).join("、")}${fx.fields.length > 10 ? ` 等 ${fx.fields.length} 个` : ""}</div>` : ""}
    ${mN.pending || mN.running ? `<div class="banner warn">${(mN.pending || 0) + (mN.running || 0)} 篇笔记的图片正在转存到本地(采集端的图片链接会过期,收到后立即下载)。</div>` : ""}
    ${(mN.error || 0) + (mN.partial || 0) ? `<div class="banner err">${(mN.error || 0) + (mN.partial || 0)} 篇笔记的图片没有完全转存成功(多为链接已过期)· <a href="#" id="rtm">全部重试</a>;让采集端重新采集同一篇,会换上新链接再转存</div>` : ""}
    <div class="tbar">
      <select data-nf="track">${opt(fa.tracks, f.track, "全部赛道")}</select>
      <select data-nf="topic">${opt(fa.topics, f.topic, "全部主题")}</select>
      <select data-nf="keyword" title="采集时用的搜索词">${opt(fa.keywords, f.keyword, "全部搜索词")}</select>
      <select data-nf="ai_type">${opt(fa.types, f.ai_type, "全部内容类型")}</select>
      <select data-nf="tier">${opt(fa.tiers, f.tier, "全部分级", k => TIER[k] || k)}</select>
      <select data-nf="ai_status">${opt(fa.ai, f.ai_status, "全部标签状态", k => AIS[k] || k)}</select>
      <select data-nf="project_id"><option value="0">全部项目</option>${projects.map(p => `<option value="${p.id}" ${String(p.id) === f.project_id ? "selected" : ""}>#${p.id} ${esc(p.track)}(${p.note_count})</option>`).join("")}</select>
      <select data-nf="sort"><option value="new" ${f.sort === "new" ? "selected" : ""}>最新入库</option><option value="hot" ${f.sort === "hot" ? "selected" : ""}>互动最高</option><option value="updated" ${f.sort === "updated" ? "selected" : ""}>最近更新</option></select>
      <input class="grow" data-nf="q" placeholder="搜索标题 / 正文 / 作者 / 标签" value="${esc(f.q)}">
      ${filtered ? `<button class="btn ghost sm" id="nreset">清除筛选</button>` : ""}</div>
    <section class="card flush"><div class="tbl-wrap"><table class="tbl"><thead><tr><th><input type="checkbox" id="ckall"></th><th></th><th>笔记</th><th>赛道 / 主题</th><th>内容类型</th><th>分级</th>
      <th class="num">赞 / 藏 / 评</th><th>来源</th><th>入库</th><th></th></tr></thead><tbody id="tb"></tbody></table></div>
      <div class="pager"><span class="sub" id="pinfo"></span><span><button class="btn gray sm" id="prev">上一页</button> <button class="btn gray sm" id="next">下一页</button></span></div></section>`;
  let total = 0, file = null, items = [];
  const qs = () => Object.entries(f).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
  const selInfo = () => {
    $("#dels").disabled = $("#retag").disabled = !state.nsel.size;
    $("#pinfo").textContent = `共 ${total} 篇 · 第 ${state.np + 1} / ${Math.max(1, Math.ceil(total / PAGE))} 页${state.nsel.size ? ` · 已选 ${state.nsel.size}` : ""}`;
  };
  const draw = async () => {
    const r = await api(`/api/admin/notes?${qs()}&offset=${state.np * PAGE}&limit=${PAGE}`);
    total = r.total; items = r.items;
    $("#tb").innerHTML = items.map(n => `<tr><td><input type="checkbox" data-id="${n.id}" ${state.nsel.has(n.id) ? "checked" : ""}></td>
      <td>${n.cover ? `<img class="thumb" data-view="${n.id}" style="cursor:pointer" src="${esc(n.cover)}" referrerpolicy="no-referrer" loading="lazy" onerror="this.style.visibility='hidden'">` : `<div class="thumb" data-view="${n.id}" style="cursor:pointer"></div>`}</td>
      <td class="clip"><a href="#" data-view="${n.id}" title="点击查看这篇笔记" style="color:inherit"><b>${n.note_type === "video" ? `<span class="tp" style="margin-right:6px">视频</span>` : ""}${esc(n.title)}</b></a>
        <span>${n.ai_highlight ? "爆点:" + esc(n.ai_highlight) : esc(n.content)}</span>
        ${n.ai_tags?.length ? `<span class="kws">${n.ai_tags.map(t => `<i>${esc(t)}</i>`).join("")}</span>` : ""}</td>
      <td class="nowrap">${esc(n.track || "-")}<div><b>${esc(n.topic || "待识别")}</b></div><div class="sub" title="采集时用的搜索词,不代表笔记内容">搜索词:${esc(n.keyword || "-")}</div>${n.rel === "low" ? `<div class="sub" style="color:var(--err)" title="${esc(n.rel_why)}">弱相关</div>` : ""}</td>
      <td class="nowrap">${n.ai_type ? `<span class="tp">${esc(n.ai_type)}</span><div class="sub">${esc(n.ai_audience)}</div>` : n.ai_status === "error" ? `<span class="st error" title="${esc(n.ai_error)}">失败</span>` : `<span class="sub">${AIS[n.ai_status] || ""}</span>`}</td>
      <td>${tierB(n.tier)}</td>
      <td class="num">${fmt(n.likes)} / ${fmt(n.collects)} / ${fmt(n.comments)}</td>
      <td class="sub nowrap">${n.source === "import" ? "导入" : esc(n.worker || "采集端")}<div>${n.project_count} 个项目</div><div>发布 ${esc(n.published_at || "-")}${n.old ? "(较早)" : ""}</div></td>
      <td class="sub nowrap">${esc(lt(n.created, "ymd"))}${n.updated && lt(n.updated, "ymd") !== lt(n.created, "ymd") ? `<div>更新 ${esc(lt(n.updated, "d"))}</div>` : ""}</td>
      <td class="nowrap"><button class="btn ghost sm" data-edit="${n.id}">编辑分类</button><button class="btn ghost sm" data-raw="${n.id}">原始数据</button></td></tr>`).join("")
      || `<tr><td colspan="10" class="sub" style="text-align:center;padding:40px">没有符合条件的笔记</td></tr>`;
    $("#prev").disabled = state.np <= 0; $("#next").disabled = state.np >= Math.ceil(total / PAGE) - 1; $("#ckall").checked = false; selInfo();
  };
  await draw();
  $$("[data-nf]").forEach(el => {
    if (el.tagName === "INPUT") { let deb; el.oninput = () => { clearTimeout(deb); deb = setTimeout(() => { f.q = el.value.trim(); state.np = 0; draw(); }, 300); }; }
    else el.onchange = () => { f[el.dataset.nf] = el.value; if (el.dataset.nf === "track") f.keyword = f.topic = ""; state.np = 0; vNotes(); };
  });
  if ($("#ftr")) $("#ftr").onclick = async e => { e.preventDefault(); const r = await api("/api/admin/notes/fill_tracks", "POST"); toast(`已开始为 ${r.n} 篇笔记判断赛道,稍后刷新页面查看`); };
  if ($("#nreset")) $("#nreset").onclick = () => { state.nf = { ...NF }; state.np = 0; vNotes(); };
  if ($("#rtm")) $("#rtm").onclick = async e => { e.preventDefault(); const { n } = await api("/api/admin/notes/remirror", "POST", {}); toast(`已重新排队 ${n} 篇`); vNotes(); };
  if ($("#rtf")) $("#rtf").onclick = async e => { e.preventDefault(); const { n } = await api("/api/admin/notes/retag", "POST", { scope: "failed" }); toast(`已重新排队 ${n} 篇`); vNotes(); };
  $("#prev").onclick = () => { state.np--; draw(); };
  $("#next").onclick = () => { state.np++; draw(); };
  $("#main").onchange = e => {
    if (e.target.id === "ckall") $$("#tb input[data-id]").forEach(c => { c.checked = e.target.checked; e.target.checked ? state.nsel.add(+c.dataset.id) : state.nsel.delete(+c.dataset.id); });
    else if (e.target.matches("#tb input[data-id]")) e.target.checked ? state.nsel.add(+e.target.dataset.id) : state.nsel.delete(+e.target.dataset.id);
    else return;
    selInfo();
  };
  $("#main").onclick = async e => {
    const vw = e.target.closest("[data-view]");
    if (vw) { e.preventDefault(); const i = items.findIndex(x => x.id === +vw.dataset.view); return viewNote(items, i); }
    const rw = e.target.closest("[data-raw]");
    if (rw) {
      const { raw } = await api(`/api/admin/notes/${rw.dataset.raw}/raw`), t = items.find(x => x.id === +rw.dataset.raw);
      const m = document.createElement("div"); m.className = "mask";
      m.innerHTML = `<div class="card" style="width:100%;max-width:760px;max-height:86vh;display:flex;flex-direction:column;padding:22px">
        <div class="card-h"><b>原始数据</b><span class="sub">${esc(t?.title || "")} · 采集端传来的完整内容,字段原样保留</span><button class="btn ghost sm" data-x style="margin-left:auto">关闭</button></div>
        <pre style="flex:1;overflow:auto;margin:0;white-space:pre-wrap;word-break:break-all;font:12px/1.65 ui-monospace,Consolas,monospace;background:var(--soft);padding:12px;border-radius:var(--r-sm)">${esc(Object.keys(raw).length ? JSON.stringify(raw, null, 2) : "(这篇笔记入库时还没有保存原始数据)")}</pre></div>`;
      m.onclick = ev => { if (ev.target === m || ev.target.closest("[data-x]")) m.remove(); };
      return document.body.appendChild(m);
    }
    const ed = e.target.closest("[data-edit]"); if (!ed) return;
    const n = items.find(x => x.id === +ed.dataset.edit);
    const m = document.createElement("div"); m.className = "mask";
    m.innerHTML = `<form class="card" style="width:100%;max-width:420px;padding:24px"><div class="card-h"><b>编辑分类</b></div><p class="sub" style="margin:-6px 0 4px">${esc(n.title)}</p>
      <label class="f">赛道</label><select id="et" style="width:100%">${trackNames.includes(n.track) ? "" : `<option selected>${esc(n.track)}</option>`}${trackNames.map(t => `<option ${t === n.track ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>
      <label class="f">主题<span class="sub"> · 这篇笔记主要讲什么,如「姜黄粉」「速溶咖啡」</span></label><input id="etp" value="${esc(n.topic)}">
      <label class="f">搜索词<span class="sub"> · 采集时用的,仅作来源记录</span></label><input id="ek" value="${esc(n.keyword)}">
      <label class="f">内容类型</label><select id="ety" style="width:100%"><option value="">(未分类)</option>${fa.content_types.map(t => `<option ${t === n.ai_type ? "selected" : ""}>${t}</option>`).join("")}</select>
      <div class="row" style="margin-top:18px;justify-content:flex-end"><button type="button" class="btn gray" data-x>取消</button><button class="btn">保存</button></div></form>`;
    m.onclick = ev => { if (ev.target === m || ev.target.closest("[data-x]")) m.remove(); };
    m.querySelector("form").onsubmit = async ev => {
      ev.preventDefault();
      try { await api(`/api/admin/notes/${n.id}`, "PUT", { track: $("#et").value, keyword: $("#ek").value.trim(), ai_type: $("#ety").value, topic: $("#etp").value.trim() }); m.remove(); toast("已保存"); draw(); } catch (err) { toast(err.message, 1); }
    };
    document.body.appendChild(m);
  };
  $("#dels").onclick = async () => {
    if (!confirm(`从笔记库彻底删除选中的 ${state.nsel.size} 篇笔记?所有项目里都会移除`)) return;
    await api("/api/admin/notes/delete", "POST", { ids: [...state.nsel] }); state.nsel.clear(); toast("已删除"); vNotes();
  };
  $("#retag").onclick = async () => { const { n } = await api("/api/admin/notes/retag", "POST", { ids: [...state.nsel] }); state.nsel.clear(); toast(`已重新排队 ${n} 篇`); vNotes(); };
  $("#tpc").onclick = async () => {
    const m = document.createElement("div"); m.className = "mask";
    const draw = items => items.length ? items.map(t => `<label class="tprow"><input type="checkbox" data-t="${esc(t.k)}" style="width:16px"><b>${esc(t.k)}</b><span class="sub">${t.n} 篇</span></label>`).join("") : `<div class="sub" style="padding:8px">还没有主题</div>`;
    m.innerHTML = `<div class="card" style="width:100%;max-width:540px;padding:24px;max-height:88vh;display:flex;flex-direction:column"><div class="card-h"><b>整理主题</b><button class="btn ghost sm" data-x style="margin-left:auto">关闭</button></div>
      <p class="sub" style="margin:-6px 0 12px">勾选几个意思相近的主题,合并成一个名字(如把「珠海咖啡店」「厦门咖啡厅」合并成「咖啡店探店」)。只勾一个就是改名。</p>
      <div class="row" style="margin-bottom:10px"><button class="btn gray sm" id="tai">AI 建议合并</button><span class="sub" id="tas">让 AI 找出该合并的主题,你确认后才会应用</span></div><div id="tsg"></div>
      <div id="tl" style="overflow:auto;flex:1;border:1px solid var(--line);border-radius:8px;padding:6px 10px;min-height:120px"></div>
      <div class="row" style="margin-top:12px"><input id="tto" class="grow" placeholder="合并成(不超过 12 个字)" maxlength="12"><button class="btn" id="tgo">合并 / 改名</button></div></div>`;
    const reload = async () => { m.querySelector("#tl").innerHTML = draw((await api("/api/admin/topics")).items); };
    await reload();
    m.onclick = ev => { if (ev.target === m || ev.target.closest("[data-x]")) { m.remove(); vNotes(); } };
    m.querySelector("#tgo").onclick = async () => {
      const names = [...m.querySelectorAll("input[data-t]:checked")].map(i => i.dataset.t), to = m.querySelector("#tto").value.trim();
      if (!names.length || !to) return toast("请勾选主题,并填写合并后的名称", 1);
      try { const r = await api("/api/admin/topics/merge", "POST", { names, to }); toast(`已把 ${r.n} 篇笔记归到「${r.to}」`); m.querySelector("#tto").value = ""; await reload(); } catch (err) { toast(err.message, 1); }
    };
    m.querySelector("#tai").onclick = async e => {
      e.target.disabled = true; m.querySelector("#tas").textContent = "AI 正在分析…";
      try {
        const { groups } = await api("/api/admin/topics/suggest", "POST", {}), box = m.querySelector("#tsg");
        m.querySelector("#tas").textContent = groups.length ? `建议合并 ${groups.length} 组,取消勾选的不会应用` : "没有需要合并的主题";
        box.innerHTML = groups.length ? `<div style="border:1px solid var(--line);border-radius:8px;padding:6px 10px;margin-bottom:10px;max-height:200px;overflow:auto">${groups.map((g, i) => `<label class="tprow"><input type="checkbox" data-g="${i}" checked style="width:16px"><span><b>${esc(g.to)}</b> <span class="sub">← ${g.from.map(esc).join("、")}(${g.n} 篇)</span></span></label>`).join("")}</div><div class="row" style="margin-bottom:10px"><button class="btn sm" id="tapply">应用选中的建议</button></div>` : "";
        if (groups.length) box.querySelector("#tapply").onclick = async ev => {
          ev.target.disabled = true; let moved = 0;
          for (const cb of box.querySelectorAll("input[data-g]:checked")) { const g = groups[+cb.dataset.g]; moved += (await api("/api/admin/topics/merge", "POST", { names: [...g.from, g.to], to: g.to })).n; }
          toast(`已整理,共调整 ${moved} 篇笔记的主题`); box.innerHTML = ""; m.querySelector("#tas").textContent = ""; await reload();
        };
      } catch (err) { m.querySelector("#tas").textContent = ""; toast(err.message, 1); }
      e.target.disabled = false;
    };
    document.body.appendChild(m);
  };
  $("#imp").onclick = () => { $("#impbox").hidden = !$("#impbox").hidden; };
  $("#exp").onclick = async () => {
    const [{ items: arcs }, ov] = await Promise.all([api("/api/admin/archive"), api("/api/admin/overview")]);
    const sel = [...state.nsel], m = document.createElement("div"); m.className = "mask";
    const list = a => a.length ? a.map(x => `<div style="display:flex;justify-content:space-between;gap:12px;padding:4px 0;font-size:13px"><a href="/api/admin/archive/${encodeURIComponent(x.name)}">${esc(x.name)}</a><span class="sub">${x.size_mb} MB · ${esc(x.time)}</span></div>`).join("") : `<div class="sub">还没有备份</div>`;
    m.innerHTML = `<div class="card" style="width:100%;max-width:580px;padding:24px;max-height:88vh;overflow:auto"><div class="card-h"><b>导出笔记库备份</b><button class="btn ghost sm" data-x style="margin-left:auto">关闭</button></div>
      <p class="sub" style="margin:-6px 0 14px">打成一个压缩包留作长期资料:完整数据(含采集端原始字段、AI 标签)、Excel 表格、图片。以后可以在「导入笔记」里直接重新导入。</p>
      <label style="display:flex;gap:8px;align-items:center;margin-bottom:6px"><input type="radio" name="sc" value="all" checked style="width:16px">全部 ${ov.notes} 篇</label>
      ${sel.length ? `<label style="display:flex;gap:8px;align-items:center;margin-bottom:6px"><input type="radio" name="sc" value="sel" style="width:16px">仅选中的 ${sel.length} 篇</label>` : ""}
      <label style="display:flex;gap:8px;align-items:center;margin:10px 0 14px"><input type="checkbox" id="wimg" checked>包含图片(图片链接会过期,建议带上)</label>
      <div class="row"><button class="btn" id="go">开始导出</button><span class="sub" id="gs"></span></div><div id="gr" style="margin-top:12px"></div>
      <div class="card-h" style="margin:18px 0 6px"><b>已有备份</b><span class="sub">保存在项目的 data/archive/ 目录</span></div><div id="al">${list(arcs)}</div></div>`;
    m.onclick = ev => { if (ev.target === m || ev.target.closest("[data-x]")) m.remove(); };
    m.querySelector("#go").onclick = async e => {
      e.target.disabled = true; m.querySelector("#gs").textContent = "正在打包…";
      try {
        const ids = m.querySelector('input[name=sc]:checked').value === "sel" ? sel : [];
        const r = await api("/api/admin/archive", "POST", { ids, with_images: m.querySelector("#wimg").checked });
        m.querySelector("#gr").innerHTML = `<div class="banner ok">✓ 已生成 <a href="/api/admin/archive/${encodeURIComponent(r.name)}">${esc(r.name)}</a>(${r.notes} 篇 · ${r.images} 张图 · ${r.size_mb} MB)</div>`;
        m.querySelector("#al").innerHTML = list((await api("/api/admin/archive")).items); m.querySelector("#gs").textContent = "";
      } catch (err) { m.querySelector("#gs").textContent = ""; toast(err.message, 1); }
      e.target.disabled = false;
    };
    document.body.appendChild(m);
  };
  const bindFile = () => { $("#file").onchange = e => e.target.files[0] && pick(e.target.files[0]); };
  const pick = fl => { file = fl; $("#drop").innerHTML = `<b>${esc(fl.name)}</b><br><span>${(fl.size / 1024).toFixed(1)} KB · 点击重新选择</span><input type="file" id="file" accept=".json,.xlsx,.csv" hidden>`; bindFile(); };
  bindFile();
  const drop = $("#drop");
  drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); e.dataTransfer.files[0] && pick(e.dataTransfer.files[0]); };
  $("#ido").onclick = async () => {
    if (!file) return toast("请先选择文件", 1);
    if (!$("#itrack").value) return toast("请选择归入的赛道", 1);
    $("#ido").disabled = true;
    try {
      const r = await api(`/api/admin/notes/import?filename=${encodeURIComponent(file.name)}&track=${encodeURIComponent($("#itrack").value)}&keyword=${encodeURIComponent($("#ikw").value)}&project_id=${$("#iproj").value}`, "POST", null, await file.arrayBuffer());
      toast(`导入完成:新入库 ${r.new} 篇,已存在并更新 ${r.updated} 篇${r.skipped ? `,跳过 ${r.skipped} 行` : ""}`); state.np = 0; vNotes();
    } catch (e) { toast(e.message, 1); }
    $("#ido").disabled = false;
  };
}

// ---------- 赛道配置 ----------
function kbEditor(title, hint, value, onSave) {
  const m = document.createElement("div"); m.className = "mask";
  m.innerHTML = `<form class="card" style="width:100%;max-width:720px;padding:24px"><div class="card-h"><b>${esc(title)}</b></div>
    <p class="sub" style="margin:-6px 0 10px">${hint}</p><textarea id="kbt" rows="16" style="font-size:13px">${esc(value)}</textarea>
    <div class="row" style="margin-top:14px;justify-content:flex-end"><button type="button" class="btn gray" data-x>取消</button><button class="btn">保存</button></div></form>`;
  m.onclick = ev => { if (ev.target === m || ev.target.closest("[data-x]")) m.remove(); };
  m.querySelector("form").onsubmit = async ev => { ev.preventDefault(); try { await onSave($("#kbt").value); m.remove(); toast("已保存"); } catch (err) { toast(err.message, 1); } };
  document.body.appendChild(m); $("#kbt").focus();
}
async function vTracks() {
  const [ts, conf] = await Promise.all([api("/api/admin/tracks"), api("/api/admin/settings")]);
  $("#pa").innerHTML = `<button class="btn gray" id="kbc">编辑通用规则</button><button class="btn" id="add">＋ 新增赛道</button>`;
  $("#main").innerHTML = `<section class="card kbcard"><div class="card-h"><b>知识库怎么用</b></div>
    <p class="sub" style="margin:0">拆解和复刻时,只会带上「通用规则」和该项目所属赛道的那一份知识库,不会把所有赛道都塞给模型。写平台规则、品类标题公式、必写信息、敏感点;被限流或审核不过时把原因补进来。</p>
    <div class="kbprev">${esc((conf.kb_common || "").slice(0, 160))}${(conf.kb_common || "").length > 160 ? "…" : ""}</div></section>
  <p class="sub" style="margin:16px 0 12px">关键词用逗号或空格分隔;关闭的赛道不会在用户端显示。修改后点该行的「保存」。</p>
  <section class="card flush" id="list">${ts.map((t, i) => `<div class="trow ${t.enabled ? "" : "off"}" data-id="${t.id}">
      <div class="ord"><button data-up="${i}" title="上移">▲</button><button data-down="${i}" title="下移">▼</button></div>
      <input data-f="name" value="${esc(t.name)}" placeholder="赛道名称">
      <input class="kws" data-f="keywords" value="${esc(t.keywords.join(","))}" placeholder="推荐关键词,逗号分隔">
      <label class="switch"><input type="checkbox" data-f="enabled" ${t.enabled ? "checked" : ""}><i></i>启用</label>
      <span class="nowrap"><button class="btn ghost sm" data-kb>知识库${t.knowledge ? "" : "(空)"}</button><button class="btn gray sm" data-save>保存</button><button class="btn ghost sm danger" data-del>删除</button></span></div>`).join("")
    || `<div class="empty" style="border:0">还没有赛道</div>`}</section>`;
  const val = row => ({ name: row.querySelector("[data-f=name]").value.trim(), keywords: row.querySelector("[data-f=keywords]").value.split(/[,,\s]+/).filter(Boolean), enabled: row.querySelector("[data-f=enabled]").checked });
  $("#main").onclick = async e => {
    const row = e.target.closest(".trow"); if (!row) return;
    const id = +row.dataset.id;
    try {
      if (e.target.closest("[data-save]")) { await api(`/api/admin/tracks/${id}`, "PUT", val(row)); toast("已保存"); return vTracks(); }
      if (e.target.closest("[data-kb]")) {
        const t = ts.find(x => x.id === id);
        return kbEditor(`${t.name} · 赛道知识库`, "建议写:标题常用公式、必须写清的信息、敏感/违规点、首图要求。用短句列表。", t.knowledge || "",
          async v => { await api(`/api/admin/tracks/${id}`, "PUT", { ...val(row), knowledge: v }); vTracks(); });
      }
      if (e.target.closest("[data-del]")) { if (confirm("删除这个赛道?已有项目不受影响")) { await api(`/api/admin/tracks/${id}`, "DELETE"); vTracks(); } return; }
      const up = e.target.dataset.up, down = e.target.dataset.down;
      if (up !== undefined || down !== undefined) {
        const ids = ts.map(t => t.id), i = +(up ?? down), j = up !== undefined ? i - 1 : i + 1;
        if (j < 0 || j >= ids.length) return;
        [ids[i], ids[j]] = [ids[j], ids[i]]; await api("/api/admin/tracks/order", "POST", { ids }); vTracks();
      }
    } catch (err) { toast(err.message, 1); }
  };
  $("#kbc").onclick = () => kbEditor("通用规则(所有赛道)", "对所有赛道生效的小红书平台规则和写作要求。", conf.kb_common || "",
    async v => { await api("/api/admin/settings", "POST", { kb_common: v }); vTracks(); });
  $("#add").onclick = async () => {
    const name = prompt("新赛道名称"); if (!name?.trim()) return;
    try { await api("/api/admin/tracks", "POST", { name, keywords: [] }); vTracks(); } catch (e) { toast(e.message, 1); }
  };
}

// ---------- 合规词库 ----------
async function vRules() {
  const { categories: CAT, rules } = await api("/api/admin/rules");
  const cur = state.rcat ||= Object.keys(CAT)[0];
  const list = rules.filter(r => r.category === cur);
  $("#pa").innerHTML = `<button class="btn gray" id="try">试扫一段文字</button>`;
  $("#main").innerHTML = `<section class="card" id="trybox" hidden style="margin-bottom:16px"><div class="card-h"><b>试扫</b><span class="sub">粘贴标题和正文,看看会命中哪些词</span></div>
      <input id="tt" placeholder="标题"><textarea id="tc" rows="4" placeholder="正文" style="margin-top:8px"></textarea>
      <div class="row" style="margin-top:10px"><button class="btn" id="tgo">扫描</button><span id="tres" class="sub"></span></div><div id="thits"></div></section>
    <div class="tbar"><div class="seg">${Object.entries(CAT).map(([k, c]) => `<button class="${k === cur ? "on" : ""}" data-cat="${k}">${c.name}<em>${rules.filter(r => r.category === k).length}</em></button>`).join("")}</div>
      <span class="sub">${CAT[cur].severity === "high" ? "高风险:命中必须改,会触发自动修正" : "低风险:只提示,建议改"}</span></div>
    <section class="card flush"><div class="tbl-wrap"><table class="tbl rtbl"><thead><tr><th>词</th><th>建议替换</th><th>排除短语(逗号分隔,出现在这些短语里不算命中)</th><th>备注 / 补充日期</th><th>启用</th><th></th></tr></thead>
      <tbody>
        <tr class="newrow"><td><input data-n="word" placeholder="新词"></td><td><input data-n="replacement" placeholder="建议怎么改"></td><td><input data-n="exclude" placeholder="如:第一步,第一次"></td>
          <td><input data-n="note" value="${new Date().toISOString().slice(0, 10)} 补充"></td><td></td><td><button class="btn sm" id="radd">添加</button></td></tr>
        ${list.map(r => `<tr data-id="${r.id}" class="${r.enabled ? "" : "off"}"><td><input data-f="word" value="${esc(r.word)}"></td><td><input data-f="replacement" value="${esc(r.replacement)}"></td>
          <td><input data-f="exclude" value="${esc(r.exclude)}"></td><td><input data-f="note" value="${esc(r.note)}"></td>
          <td><label class="switch"><input type="checkbox" data-f="enabled" ${r.enabled ? "checked" : ""}><i></i></label></td>
          <td class="nowrap"><button class="btn gray sm" data-rs>保存</button><button class="btn ghost sm danger" data-rd>删除</button></td></tr>`).join("")}
      </tbody></table></div></section>`;
  const rowVal = tr => ({ category: cur, word: tr.querySelector("[data-f=word]").value.trim(), replacement: tr.querySelector("[data-f=replacement]").value,
    exclude: tr.querySelector("[data-f=exclude]").value, note: tr.querySelector("[data-f=note]").value, enabled: tr.querySelector("[data-f=enabled]").checked });
  $("#main").onclick = async e => {
    const c = e.target.closest("[data-cat]"); if (c) { state.rcat = c.dataset.cat; return vRules(); }
    const tr = e.target.closest("tr[data-id]");
    try {
      if (e.target.closest("[data-rs]")) { await api(`/api/admin/rules/${tr.dataset.id}`, "PUT", rowVal(tr)); toast("已保存"); return vRules(); }
      if (e.target.closest("[data-rd]") && confirm("删除这个词?")) { await api(`/api/admin/rules/${tr.dataset.id}`, "DELETE"); return vRules(); }
    } catch (err) { toast(err.message, 1); }
  };
  $("#radd").onclick = async () => {
    const v = k => $(`.newrow [data-n=${k}]`).value;
    if (!v("word").trim()) return toast("请输入词", 1);
    try { await api("/api/admin/rules", "POST", { category: cur, word: v("word"), replacement: v("replacement"), exclude: v("exclude"), note: v("note") }); toast("已添加"); vRules(); } catch (err) { toast(err.message, 1); }
  };
  $("#try").onclick = () => { $("#trybox").hidden = !$("#trybox").hidden; };
  $("#tgo").onclick = async () => {
    const r = await api("/api/admin/rules/scan", "POST", { title: $("#tt").value, content: $("#tc").value });
    $("#tres").textContent = r.hits.length ? `高风险 ${r.high} · 建议改 ${r.low}` : "没有命中";
    $("#thits").innerHTML = r.hits.map(h => `<div class="chk-i ${h.severity}"><span>${esc(h.category)}</span><b>「${esc(h.word)}」</b><span class="sub">${h.field === "title" ? "标题" : "正文"}${h.count > 1 ? ` ×${h.count}` : ""} → ${esc(h.replacement)}</span></div>`).join("");
  };
}

// ---------- 接口配置 ----------
async function vSettings() {
  const s = await api("/api/admin/settings");
  const G = [
    ["DeepSeek", "负责拆解爆款、规划角度、写标题正文和生图提示词", { deepseek_base_url: "接口地址", deepseek_model: "模型", deepseek_api_key: "API Key" }],
    ["生图模型", "按 OpenAI 兼容的 /images/generations 调用,如 https://xxx/v1;同时支持「直接返回图片」和「返回任务号、轮询取图」两种。格式不同的服务商需要改 app/imagegen.py", { image_base_url: "接口地址", image_model: "模型名", image_api_key: "API Key", image_size: "图片尺寸", image_resolution: "清晰度" }],
    ["复刻", "并发是同时处理的篇数,太大可能触发接口限流;一篇里的几张图会并行生成,所有生图请求共用「生图接口同时请求数」这个上限(默认 4,遇到 429 限流就调小);自动修正:成稿命中高风险词时让 DeepSeek 按扫描结果改一次;超长自动压缩:正文明显超过爆款组字数且基准可信时压缩一次,原稿保留可切换;自动审稿:成稿后对照拆解卡打分并给出修改版;模拟评论区会多用一些 token(均为 1 开 / 0 关)", { text_concurrency: "写文案并发", image_concurrency: "生图并发(同时处理的稿子数)", image_api_concurrency: "生图接口同时请求数(所有生图共用,遇到限流就调小)", auto_fix: "自动修正(1/0)", auto_compress: "超长自动压缩(1/0)", image_count_weights: "每篇图片张数比例(1/2/3/4 张,逗号分隔)", auto_review: "自动审稿(1/0)", review_comments: "审稿附模拟评论区(1/0)", title_hint: "标题写法参考(1/0):按千帆真实笔记里明显有效的写法给写标题的提示" }],
    ["产品图", "员工上传产品图后:系统用「看图模型」识别产品、定赛道和搜索词;配图时把产品图作为参考,保证产品外观一致;可以让最后几张直接用产品原图(包装 100% 真实,默认 0 = 全部 AI 生成)。看图接口按 OpenAI 兼容的 /chat/completions 调用:用千问填 https://dashscope.aliyuncs.com/compatible-mode/v1 和 qwen-vl-max;地址和 Key 留空则沿用生图接口的", {
      vision_base_url: "看图接口地址", vision_api_key: "看图接口 API Key", vision_model: "看图模型", product_real_slots: "最后几张直接用产品原图(0 = 不用)", product_max_refs: "AI 生图最多参考几张产品图" }],
    ["批量自动化", "员工点「一键生成」后后台自动跑完整条流水线的参数。合格线:审稿总分达到多少才算合格;不合格的稿子自动重写(次数见「重写次数」),仍不合格会标成「需检查」", {
      auto_pass_score: "合格线(审稿总分 ≥)", auto_retry: "不合格自动重写次数", auto_max_count: "一次最多生成几篇", auto_images: "是否配图(1/0)",
      auto_refs: "每个主题取几篇做参考", auto_max_topics: "最多拆解几个主题", auto_max_keywords: "每个任务最多用几个关键词", auto_default_keywords: "没填关键词时取预设词个数",
      auto_library_min: "笔记库已有几篇相关笔记就跳过采集", auto_min_notes: "采到少于几篇就停下提示", auto_min_relevant: "相关笔记少于几篇就补充搜索词", auto_enough_relevant: "相关笔记够多就提前结束采集(0 = 不提前)", auto_collect_wait_min: "最多等采集端(分钟)", auto_write_wait_min: "写稿配图最多等(分钟)" }],
    ["爆款回流", "发出去的稿子和千帆里的笔记表现按标题自动对上号,按下面的门槛判定谁爆了,员工在「复盘」里一键以爆款为样本继续复刻。爆款 = 发布满 N 天、互动(赞+藏+评)不少于下限、且达到本账号平均的 X 倍。「自动继续复刻」打开后,新出现的爆款会自动复刻(每个爆款最多连续复刻 3 代),会消耗文案和生图额度,每天有篇数上限,默认关闭",
      { loop_win_lift: "爆款倍数(≥ 账号平均的几倍)", loop_min_eng: "爆款互动下限(赞+藏+评 ≥)", loop_min_days: "发布满几天才下结论", loop_spawn_count: "继续复刻默认篇数", loop_auto: "自动继续复刻(1/0)", loop_daily_cap: "自动复刻每天最多几篇" }],
    ["达人采集", "联系方式:员工在达人库里点「获取联系方式」,采集端才会像人一样在千帆买手广场打开那位达人的邀约弹窗、点眼睛读手机号 / 微信号(绝不点弹窗里的「发起邀约」提交按钮),每天有上限,默认关闭,联系方式只用于洽谈合作。让采集端去千帆「买手广场」采集合作达人的公开信息(昵称、粉丝、合作品类、历史合作、场均销售额、客单价、合作意愿),员工在「达人库」里筛选和记录建联。需要同时打开「千帆数据同步」的总开关。不采集手机号和微信号。采集品类写买手广场「合作品类」一级品类名的前几个字就行(如 保健食品、粮油调味),留空就用目标品类;达人历史合作品类命中目标品类 = 高适配,只有内容分类命中相关内容分类 = 一般(改完自动重算)。每个品类按列表顺序最多取「翻页数 × 20」位",
      { kol_enabled: "自动采集(1/0)", kol_hours: "多久更新一次(小时)", kol_cats: "采集的合作品类(逗号分隔,留空 = 目标品类)", kol_targets: "我们的目标品类关键词(逗号分隔,对应达人历史合作品类的一级品类名)", kol_content: "相关内容分类关键词(逗号分隔,如 美食)", kol_pages: "每个品类最多翻几页", kol_contact_enabled: "允许员工获取达人联系方式(1/0,默认关)", kol_contact_cap: "每天最多获取几位的联系方式", kol_contact_paused: "自动暂停原因(遇到风控时自动写入;重新打开前清空)" }],
    ["采集", "新建项目或追加任务时,记在任务上交给采集端:抓多少条、只要近多少天发布的、什么类型、怎么排序。默认只采近半年(180 天)的图文;太旧的笔记即使采集端漏过滤,工作台收到时也会丢掉", { collect_limit: "每个任务采集条数", collect_max_age_days: "只采近多少天的笔记(0 = 不限)", collect_note_type: "笔记类型(image 只要图文 / all 不限)", collect_sort: "排序(popularity 最热 / general 综合 / latest 最新)" }],
    ["帖子分类", "入库后由 DeepSeek 自动打内容标签(1 开 / 0 关);分级按 赞+藏+评 计算,改阈值立即生效", { auto_tag: "自动打标签(1/0)", tier_viral: "爆款阈值(互动量 ≥)", tier_hot: "热门阈值(互动量 ≥)" }],
    ["图片转存", "采集端回传的图片链接带签名、会过期。开启后收到笔记就立即下载到服务器本地,页面都用本地副本(1 开 / 0 关)", { mirror_images: "自动转存(1/0)", mirror_concurrency: "同一篇同时下载几张图" }],
  ];
  const wide = k => k.endsWith("base_url");
  const SIZES = [["9:16", "9:16 竖屏"], ["3:4", "3:4 竖图"], ["4:5", "4:5 竖图"], ["2:3", "2:3 竖图"], ["1:1", "1:1 正方形"], ["4:3", "4:3 横图"], ["16:9", "16:9 横屏"]];
  const RES = [["", "不指定(服务商默认)"], ["1k", "1k 标准"], ["2k", "2k 高清"], ["4k", "4k 超清"]];
  const field = (k, label) => {
    if (k === "image_size") {
      const known = SIZES.some(x => x[0] === s[k]);
      return `<div><label class="f">${label}<span class="sub"> · 比例,部分服务商用像素</span></label><select id="img-size" style="width:100%">${SIZES.map(([v, n]) => `<option value="${v}" ${v === s[k] ? "selected" : ""}>${n}</option>`).join("")}<option value="__custom__" ${known ? "" : "selected"}>自定义(如 1024x1536)</option></select>
        <input id="img-size-custom" placeholder="例如 1024x1536" value="${known ? "" : esc(s[k])}" style="margin-top:8px;${known ? "display:none" : ""}"></div>`;
    }
    if (k === "image_resolution") return `<div><label class="f">${label}<span class="sub"> · 越高越贵越慢</span></label><select id="img-res" style="width:100%">${RES.map(([v, n]) => `<option value="${v}" ${v === (s[k] || "") ? "selected" : ""}>${n}</option>`).join("")}</select></div>`;
    return `<div class="${wide(k) ? "wide" : ""}"><label class="f">${label}</label><input data-k="${k}" ${k.includes("key") ? 'type="password" autocomplete="off"' : ""} value="${esc(s[k])}"></div>`;
  };
  const TESTS = {
    "DeepSeek": `<div class="testrow"><button class="btn gray sm" data-test="deepseek">测试连通</button><span class="sub">用上面填的内容发一条很短的消息,几乎不花钱,不用先保存</span><div class="tres"></div></div>`,
    "达人采集": `<div class="testrow"><button class="btn gray sm" data-kolsync>立即采集</button><span class="sub">先保存上面的设置,再点:给采集端下发买手广场采集任务(采集端需要在线,并且已升级到支持达人采集的版本)</span><div class="tres"></div></div>`,
    "产品图": `<div class="testrow"><button class="btn gray sm" data-test="vision">测试看图</button><span class="sub">用一张自带的小图问模型是什么颜色,几乎不花钱,不用先保存</span><div class="tres"></div></div>`,
    "生图模型": `<div class="testrow"><button class="btn gray sm" data-test="image">测试连通</button><button class="btn gray sm" data-test="image_generate">试生成一张图</button><span class="sub">「测试连通」免费;「试生成一张图」会真实生图并按 1 张计费</span><div class="tres"></div></div>`,
  };
  const formVals = () => {
    const d = {}; $$("#main [data-k]").forEach(i => d[i.dataset.k] = i.value.trim());
    if ($("#img-size")) d.image_size = $("#img-size").value === "__custom__" ? $("#img-size-custom").value.trim() : $("#img-size").value;
    if ($("#img-res")) d.image_resolution = $("#img-res").value;
    return d;
  };
  $("#pa").innerHTML = `<button class="btn" id="sv">保存配置</button>`;
  const base = (s.public_base_url || "").replace(/\/+$/, "") || location.origin;
  const local = /\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/|$)/.test(base);
  $("#main").innerHTML = G.map(([h, p, L]) => `<div class="sg"><div><h3>${h}</h3><p>${esc(p)}</p></div><div class="card">
    ${Object.keys(L).map(k => field(k, L[k])).join("")}
    ${TESTS[h] || ""}</div></div>`).join("") + `<div class="sg"><div><h3>采集端对接</h3><p>把下面的地址和 Token 交给采集端。完整字段标准见项目里的 docs/采集端接口.md</p></div><div class="card" style="display:block">
      <label class="f" style="margin-top:0">对外地址<span class="sub"> · 采集端访问工作台用的地址,同一局域网填本机局域网 IP,如 http://192.168.1.100:18765</span></label>
      <div class="row"><input data-k="public_base_url" class="grow" value="${esc(s.public_base_url || "")}" placeholder="http://本机局域网IP:18765"></div>
      ${local ? `<div class="banner warn" style="margin:10px 0 0">当前显示的是 ${esc(base)},只有本机能访问。采集端在别的电脑上时,请填上对外地址并保存。</div>` : ""}
      <div class="endpoint" style="margin-top:10px"><span class="m">GET</span><code class="mono">${esc(base)}/api/ping?worker=名称</code><span class="sub">连通自检</span></div>
      <div class="endpoint"><span class="m">GET</span><code class="mono">${esc(base)}/api/tasks?limit=1&amp;worker=名称</code><span class="sub">领取任务</span></div>
      <div class="endpoint"><span class="m post">POST</span><code class="mono">${esc(base)}/api/notes</code><span class="sub">回传笔记</span></div>
      <label class="f">采集端 Token<span class="sub"> · 请求头 Authorization: Bearer &lt;Token&gt;</span></label>
      <div class="tokenbox"><input id="tok" type="password" readonly value="${esc(s.collector_token)}">
        <button class="btn gray sm" id="tshow">显示</button><button class="btn gray sm" id="tcopy">复制</button><button class="btn ghost sm danger" id="treset">重新生成</button></div>
    </div></div>`;
  if ($("#img-size")) $("#img-size").onchange = e => { $("#img-size-custom").style.display = e.target.value === "__custom__" ? "" : "none"; };
  $("#main").onclick = async e => {
    const ks = e.target.closest("[data-kolsync]");
    if (ks) {
      const out = ks.closest(".testrow").querySelector(".tres"); ks.disabled = true;
      try { const r = await api("/api/admin/kol/sync", "POST"); out.innerHTML = `<span class="st done">${r.created ? `已下发 ${r.created} 个采集任务` : "已有未完成的采集任务,不重复下发"}</span>`; } catch (err) { out.innerHTML = `<span class="st error">失败</span> ${esc(err.message)}`; }
      ks.disabled = false; return;
    }
    const b = e.target.closest("[data-test]"); if (!b) return;
    const kind = b.dataset.test, row = b.closest(".testrow"), out = row.querySelector(".tres");
    if (kind === "image_generate" && !confirm("会用上面填的配置真实生成 1 张图,按 1 张图计费。继续?")) return;
    $$("button", row).forEach(x => x.disabled = true);
    out.innerHTML = `<span class="st run">${kind === "image_generate" ? "生成中,通常要 30-90 秒…" : "测试中…"}</span>`;
    try {
      const r = await api(`/api/admin/test/${kind}`, "POST", formVals());
      out.innerHTML = `<span class="st ${r.ok ? "done" : r.ok === false ? "error" : ""}">${r.ok ? "成功" : r.ok === false ? "失败" : "提示"}</span> <span>${esc(r.msg || "")}</span>`
        + (r.url ? `<img src="${esc(r.url)}?t=${Date.now()}" style="display:block;margin-top:10px;max-height:300px;border-radius:8px;border:1px solid var(--line)">` : "");
    } catch (err) { out.innerHTML = `<span class="st error">失败</span> ${esc(err.message)}`; }
    $$("button", row).forEach(x => x.disabled = false);
  };
  // 设置分组很多、页面很长:顶部加一条吸顶的分组导航,点一下滚到对应分组
  const groups = $$("#main > .sg");
  $("#main").insertAdjacentHTML("afterbegin", `<nav class="sg-nav" aria-label="设置分组">${groups.map((g, i) => `<button class="chip" data-sg="${i}">${esc(g.querySelector("h3").textContent)}</button>`).join("")}</nav>`);
  $(".sg-nav").onclick = e => { const b = e.target.closest("[data-sg]"); if (b) groups[+b.dataset.sg].scrollIntoView({ behavior: "smooth", block: "start" }); };
  $("#sv").onclick = async () => {
    const d = formVals();
    try { await api("/api/admin/settings", "POST", d); toast("配置已保存"); } catch (e) { toast(e.message, 1); }
  };
  $("#tshow").onclick = e => { const t = $("#tok"); t.type = t.type === "password" ? "text" : "password"; e.target.textContent = t.type === "password" ? "显示" : "隐藏"; };
  $("#tcopy").onclick = async () => { try { await navigator.clipboard.writeText($("#tok").value); toast("已复制"); } catch { $("#tok").type = "text"; $("#tok").select(); toast("请手动复制", 1); } };
  $("#treset").onclick = async () => {
    if (!confirm("重新生成后,旧 Token 立即失效,采集端需要换成新 Token。继续?")) return;
    const { token } = await api("/api/admin/collector_token/reset", "POST"); $("#tok").value = token; toast("已生成新 Token");
  };
}

// ---------- 账号安全 ----------
async function vSecurity() {
  const s = await api("/api/admin/settings");
  $("#main").innerHTML = `<div class="sg"><div><h3>管理员密码</h3><p>登录管理端用。至少 8 位。也可以在启动前设置环境变量 ADMIN_PASSWORD 来重置。</p></div>
    <form class="card" id="f1" style="display:block"><label class="f" style="margin-top:0">原密码</label><input type="password" id="old" autocomplete="current-password" required>
      <label class="f">新密码</label><input type="password" id="new1" autocomplete="new-password" minlength="8" required>
      <label class="f">再输一次</label><input type="password" id="new2" autocomplete="new-password" required>
      <div class="row" style="margin-top:16px;justify-content:flex-end"><button class="btn">修改密码</button></div></form></div>`;
  $("#f1").onsubmit = async e => {
    e.preventDefault();
    if ($("#new1").value !== $("#new2").value) return toast("两次输入的新密码不一致", 1);
    try { await api("/api/admin/password", "POST", { old: $("#old").value, new: $("#new1").value }); toast("密码已修改"); e.target.reset(); } catch (err) { toast(err.message, 1); }
  };
}

// ---------- 人员管理 ----------
const genPw = () => { const cs = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"; const a = new Uint32Array(8); crypto.getRandomValues(a); return [...a].map(x => cs[x % cs.length]).join(""); };
async function vStaff() {
  const r = await api("/api/admin/staff"), items = r.items;
  $("#pa").innerHTML = `<button class="btn" id="sadd">＋ 新增员工</button>`;
  const on = items.filter(x => x.enabled).length;
  $("#main").innerHTML = `${items.length ? "" : `<div class="banner warn">还没有员工账号,前端目前<b>任何人都能打开</b>。新增第一个员工账号后,前端就需要登录了。</div>`}
  ${r.legacy_password ? `<div class="banner warn">前端还在使用旧的「通用访问密码」。创建员工账号后它不再生效。<button class="btn ghost sm" id="slegacy">取消通用密码</button></div>` : ""}
  <p class="sub" style="margin:0 0 12px">员工用自己的账号登录前端(新建任务、看成品和数据看板)。任务会记下是谁发起的;停用、重置密码或「踢下线」后,对方已登录的页面立刻失效。共 ${items.length} 人,启用 ${on} 人。</p>
  <section class="card flush">${items.length ? `<table class="tbl"><thead><tr><th>姓名</th><th>账号</th><th>状态</th><th class="num">发起任务</th><th class="num">成品稿</th><th class="num">Live 任务</th><th>最近登录</th><th></th></tr></thead><tbody>${items.map(x => `<tr data-id="${x.id}">
    <td><b>${esc(x.name || x.username)}</b></td><td class="sub">${esc(x.username)}</td>
    <td>${x.enabled ? `<span class="st done">启用</span>` : `<span class="st">已停用</span>`}</td>
    <td class="num">${x.projects}</td><td class="num">${x.drafts}</td><td class="num">${x.live}</td>
    <td class="sub">${x.last_login ? esc(x.last_login.slice(0, 16)) + (x.last_ip ? ` · ${esc(x.last_ip)}` : "") : "从未登录"}</td>
    <td class="nowrap"><button class="btn gray sm" data-act="name">改名</button> <button class="btn gray sm" data-act="pw">重置密码</button> <button class="btn gray sm" data-act="kick">踢下线</button> <button class="btn gray sm" data-act="toggle">${x.enabled ? "停用" : "启用"}</button> <button class="btn ghost sm danger" data-act="del">删除</button></td></tr>`).join("")}</tbody></table>` : `<div class="empty" style="border:0">还没有员工</div>`}</section>`;
  const dlg = (title, body, okText, onOk) => {
    const m = document.createElement("div"); m.className = "mask";
    m.innerHTML = `<form class="card" style="width:100%;max-width:420px;padding:24px"><h3 style="margin:0 0 12px">${title}</h3>${body}<div class="row" style="margin-top:18px;justify-content:flex-end;gap:8px"><button type="button" class="btn gray" data-x>取消</button><button class="btn">${okText}</button></div></form>`;
    document.body.appendChild(m);
    const close = () => m.remove(); m.querySelector("[data-x]").onclick = close; m.onclick = e => { if (e.target === m) close(); };
    m.querySelector("form").onsubmit = async e => { e.preventDefault(); try { await onOk(m); close(); } catch (err) { toast(err.message, 1); } };
    const f = m.querySelector("input"); f && f.focus(); return m;
  };
  const pwField = `<label class="f">密码(至少 6 位)</label><div class="row" style="gap:8px"><input id="dpw" type="text" minlength="6" required autocomplete="off" style="flex:1"><button type="button" class="btn gray sm" id="dgen">随机生成</button></div><p class="sub" style="margin:8px 0 0">请把密码当面或私聊发给员工;保存后系统不会再显示它。</p>`;
  const bindGen = m => { m.querySelector("#dgen").onclick = () => { m.querySelector("#dpw").value = genPw(); }; };
  $("#sadd").onclick = () => {
    if (!items.length && !confirm("创建第一个员工账号后,前端需要登录才能使用。继续?")) return;
    const m = dlg("新增员工", `<label class="f" style="margin-top:0">账号(登录用,2~32 位字母、数字、中文或 _ . @ -)</label><input id="du" required autocomplete="off">
      <label class="f">姓名(显示用,可不填)</label><input id="dn" maxlength="20" autocomplete="off">${pwField}`, "创建", async m => {
      await api("/api/admin/staff", "POST", { username: m.querySelector("#du").value, name: m.querySelector("#dn").value, password: m.querySelector("#dpw").value });
      toast("已创建"); vStaff();
    });
    bindGen(m);
  };
  const lg = $("#slegacy");
  if (lg) lg.onclick = async () => { if (!confirm("取消通用访问密码?")) return; try { await api("/api/admin/user_password", "POST", { password: "" }); toast("已取消"); vStaff(); } catch (err) { toast(err.message, 1); } };
  $("#main").onclick = async e => {
    const b = e.target.closest("[data-act]"); if (!b) return;
    const row = b.closest("tr"), id = +row.dataset.id, x = items.find(i => i.id === id), act = b.dataset.act;
    try {
      if (act === "name") return dlg(`改名:${esc(x.username)}`, `<label class="f" style="margin-top:0">姓名</label><input id="dn" maxlength="20" value="${esc(x.name || "")}" required>`, "保存", async m => { await api(`/api/admin/staff/${id}`, "POST", { name: m.querySelector("#dn").value }); toast("已保存"); vStaff(); });
      if (act === "pw") { const m = dlg(`重置密码:${esc(x.username)}`, pwField + `<p class="sub" style="margin:8px 0 0">重置后对方当前登录会失效,需要用新密码重新登录。</p>`, "重置", async m => { await api(`/api/admin/staff/${id}/password`, "POST", { password: m.querySelector("#dpw").value }); toast("密码已重置"); vStaff(); }); return bindGen(m); }
      if (act === "kick") { if (!confirm(`让 ${x.name || x.username} 立刻下线?对方需要重新登录。`)) return; await api(`/api/admin/staff/${id}/kick`, "POST"); return toast("已踢下线"); }
      if (act === "toggle") { if (x.enabled && !confirm(`停用 ${x.name || x.username}?对方会立刻被踢下线,且不能再登录。`)) return; await api(`/api/admin/staff/${id}`, "POST", { enabled: !x.enabled }); toast(x.enabled ? "已停用" : "已启用"); return vStaff(); }
      if (act === "del") { if (!confirm(`删除 ${x.name || x.username}?他发起过的任务和成品不受影响。`)) return; await api(`/api/admin/staff/${id}`, "DELETE"); toast("已删除"); return vStaff(); }
    } catch (err) { toast(err.message, 1); }
  };
}

// ---------- 登录(左右分屏,见 login.js) ----------
function gate() {
  if ($(".gate")) return;
  clearTimeout(pollT);
  loginScreen({ admin: true, submit: async ({ password }) => {
    const r = await fetch("/api/admin/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password }) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "登录失败");
    setTimeout(render);
  } });
}

fetch("/api/version").then(r => r.json()).then(v => {
  const el = $("#ver"); if (!el) return;
  el.hidden = false; el.innerHTML = `<b>${esc(v.version)}${v.dirty ? "+" : ""}</b><span>${esc(v.commit || "本地开发")}${v.built ? " · " + esc(v.built.slice(5)) : ""}</span>`;
  el.title = [`${v.version} ${v.commit} ${v.built}`.trim(), v.subject].filter(Boolean).join("\n");
}).catch(() => { });
fetch("/api/admin/session").then(r => r.ok ? r.json() : Promise.reject()).then(s => s.ok ? render() : gate())
  .catch(() => { document.body.innerHTML = `<div class="gate"><div class="card" style="padding:32px;text-align:center;max-width:420px"><h2 style="margin:0 0 8px">服务端版本不匹配</h2><p class="sub">网页已更新,但服务还在运行旧版本。请关闭 run.bat 窗口后重新双击启动。</p></div></div>`; });
