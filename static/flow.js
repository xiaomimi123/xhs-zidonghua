// ---------- 流水线界面:新建任务 / 任务 / 成品库 / 笔记预览 ----------
// 员工只做三件事:新建任务 → 看进度 → 拿成品。原来的五步手动流程收在「高级」里。
const FL = { mode: "product", track: "", kw: "", count: 10, P: { name: "", files: [], price: "", sp: "", aud: "", forbid: "", track: "", pick: 0 },
  filter: "all", pid: 0, picking: false, picked: new Set(), pv: null, sig: "", account: "", acc: "", accts: null, plan: {}, angles: new Set(), angAcc: "", accKw: {} };
const fetchAccts = async () => FL.accts || (FL.accts = await api("/api/qianfan/accounts").catch(() => []));
const when = s => { const d = new Date((s || "").replace(" ", "T") + "Z"); return isNaN(d) ? "" : `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };
// 任务状态只分四种颜色:蓝=进行中、绿=已完成、橙=完成但有稿子要检查、红=出错,灰=已取消
const runState = r => r.active ? "run" : r.status === "done" ? (r.drafts.failed ? "warn" : "ok") : r.status === "error" ? "bad" : "off";
const runPill = r => {
  if (r.status === "done") return r.drafts.failed ? `<span class="fl-pill warn">✓ 完成 · ${r.drafts.failed} 篇需检查</span>` : `<span class="fl-pill ok">✓ 已完成</span>`;
  if (r.status === "error") return `<span class="fl-pill bad">✕ 出错</span>`;
  if (r.status === "canceled") return `<span class="fl-pill">已取消</span>`;
  const s = r.steps[r.step] || "处理";
  return `<span class="fl-pill run"><i class="dot"></i>${s === "写稿配图" ? `写稿配图 ${r.drafts.finished || 0}/${r.target}` : s + "中"}</span>`;
};
const segCls = (r, i) => i < r.step ? "done" : i === r.step ? (r.active ? "cur" : ["error", "canceled"].includes(r.status) ? "bad" : "") : "";
const runCard = r => `<article class="fl-run s-${runState(r)}" data-run="${r.id}" tabindex="0">
  <div class="fl-rh">${r.cover ? `<img src="${esc(thumb(r.cover, 240))}" alt="" loading="lazy" decoding="async">` : `<span class="fl-ph">${r.kind === "product" ? "产品" : "赛道"}</span>`}
    <div class="fl-rt"><b>${esc(r.name)}</b><span class="sub">${r.target} 篇 · ${when(r.created)}</span></div>${runPill(r)}</div>
  ${r.status === "done" ? "" : `<div class="fl-track">${r.steps.map((s, i) => `<div class="${segCls(r, i)}"><i></i><span>${s}</span></div>`).join("")}</div>`}
  <div class="fl-rf">${r.status === "done" ? `<span class="ok">合格 ${r.drafts.passed}</span>${r.drafts.failed ? `<span class="warn">需检查 ${r.drafts.failed}</span>` : ""}` : `<span class="sub fl-msg">${esc(r.msg)}</span>`}
    ${r.active ? "" : `<button class="btn gray sm" data-rerun="${r.id}" title="用同样的设置再跑一次">重新运行</button><button class="btn ghost sm danger" data-rdel="${r.id}" data-n="${r.drafts.total ?? r.drafts.finished ?? 0}" title="删除这个任务">${FL.delRun === r.id ? `再点一次确认${(r.drafts.total ?? r.drafts.finished) ? `(含 ${r.drafts.total ?? r.drafts.finished} 篇成品)` : ""}` : "删除"}</button>`}
    <span class="go">${r.status === "done" ? "查看成品 ›" : "详情 ›"}</span></div>
</article>`;
const noteCover = (d, big) => d.images.length ? `<img src="${esc(thumb("/images/" + d.images[0]))}" alt="" loading="lazy" decoding="async">` : `<div class="fl-tc${big ? " big" : ""}">${esc(d.title)}</div>`;
const noteCard = d => `<article class="fl-nc ${FL.picked.has(d.id) ? "picked" : ""}" data-note="${d.id}" tabindex="0">
  <div class="fl-cv">${noteCover(d)}${FL.picking ? `<span class="fl-sel" aria-hidden="true"></span>` : `<span class="fl-badge ${d.auto_ok === 0 ? "warn" : "ok"}">${d.auto_ok === 0 ? "需检查" : d.auto_ok === 1 ? "合格" : esc(d.grade || "")}</span>`}</div>
  <div class="fl-nt">${esc(d.title)}</div>
  <div class="fl-ns"><span>${esc(d.run)}</span>${d.account_name ? `<span class="sub">→ ${esc(d.account_name)}</span>` : ""}${d.tier === "winner" ? `<span class="pub" style="background:var(--err-wash);color:var(--err)">爆款</span>` : ""}${d.published ? `<span class="pub">已发布</span>` : ""}</div>
</article>`;

// 卡片(任务、成品、爆款)能用 Tab 聚焦,也要能用回车 / 空格打开;卡片里的按钮、下拉自己处理按键
document.addEventListener("keydown", e => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const t = e.target; if (!t.matches?.(".fl-run, .fl-mini, .fl-nc[data-note], .lp-w")) return;
  e.preventDefault(); t.click();
});

const fmtN = n => n >= 10000 ? (n / 10000).toFixed(1) + "w" : (n ?? 0);

// ---------- 产品图缩略图:点击放大、‹ › 前后移、拖动排序、× 去掉 ----------
// 生图时只取前 3 张当参考,所以顺序很重要。kind:p = 新产品的图,e = 编辑产品时的图(原图和新图可以交错),v = 只看(不能改)。
const FL_REFS = 3;
function imgTile(kind, u, i, n, rm = "") {
  const mv = kind === "v" ? "" : `<span class="fl-mv"><button type="button" data-mv="${kind}:${i}:-1" aria-label="往前移一位" ${i === 0 ? "disabled" : ""}>‹</button><button type="button" data-mv="${kind}:${i}:1" aria-label="往后移一位" ${i === n - 1 ? "disabled" : ""}>›</button></span>`;
  return `<span class="fl-up${kind === "v" ? "" : " mv"}" ${kind === "v" ? "" : `draggable="true" data-ord="${kind}:${i}"`}>${i < FL_REFS ? `<i class="fl-refno" title="生图时会用这张当参考">${i + 1}</i>` : ""}<img src="${esc(u)}" alt="第 ${i + 1} 张产品图,点击放大" data-zoom="${kind}:${i}" tabindex="0" role="button" draggable="false">${rm}${mv}</span>`;
}

// ---------- 按账号推荐 ----------
const WRITABLE_ANGLES = ["产品种草", "吃法搭配", "菜谱做法", "知识科普", "测评对比"];
function angleBlock(plan) {
  const by = Object.fromEntries(plan.angles.map(a => [a.name, a]));
  const tip = a => a && a.n ? `该账号 ${a.n} 篇,${plan.metric === "eng" ? "平均互动" : "平均阅读"}是可写角度平均的 ${a.lift_w ?? a.lift ?? "-"} 倍` : "这个账号还没发过这类";
  return `<span class="fl-lbl">这个账号适合写的选题角度 · 已按数据推荐,点选可调整</span>
    <div class="fl-chips">${WRITABLE_ANGLES.map(n => { const a = by[n]; return `<button class="chip ${FL.angles.has(n) ? "on" : ""}" data-fang="${n}" title="${esc(tip(a))}">${n}${a && a.n ? ` · ${a.n} 篇` : ""}${a?.verdict === "好" ? " ↑" : a?.verdict === "弱" ? " ↓" : ""}</button>`; }).join("")}</div>
    ${plan.note ? `<p class="sub" style="margin:6px 0 0">${esc(plan.note)}</p>` : ""}`;
}
const accKwHtml = ks => ks === undefined ? `<p class="sub">正在按这个账号的内容推荐搜索词…</p>`
  : ks.length ? `<span class="fl-lbl">按这个账号的内容推荐的搜索词 · 点一下加进关键词,不填关键词时系统默认用它们</span><div class="fl-chips">${ks.map(w => `<button class="chip" data-fhot="${esc(w)}">${esc(w)}</button>`).join("")}</div>` : "";

// ---------- 新建任务 ----------
async function fNew() {
  if (!TRACKS.length) TRACKS = (await api("/api/tracks")).map(t => [t.name, t.keywords]);
  const [list, runs, hot, accts] = await Promise.all([api("/api/products").catch(() => []), api("/api/runs?limit=40").catch(() => []),
    api(`/api/qianfan/hot?track=${encodeURIComponent(FL.track || "")}`).catch(() => ({})), fetchAccts()]);
  if (FL.account && !accts.some(a => a.account_id === FL.account)) FL.account = "";
  const plan = FL.account ? (FL.plan[FL.account] ||= await api(`/api/qianfan/account_plan?account_id=${encodeURIComponent(FL.account)}`).catch(() => null)) : null;
  if (plan && FL.angAcc !== FL.account) { FL.angles = new Set(plan.recommended); FL.angAcc = FL.account; }   // 换了账号就按这个账号的推荐重新选
  if (!FL.account) { FL.angles = new Set(); FL.angAcc = ""; }
  const P = FL.P, prod = FL.mode === "product", picked = P.pick && list.find(x => x.id === P.pick);
  if (!FL.track) FL.track = TRACKS[0]?.[0] || "";
  const accName = (accts.find(x => x.account_id === FL.account) || {}).name;
  const sum = `${prod ? (picked ? picked.name : P.name.trim() || "还没填产品") : FL.track || "还没选赛道"} · ${FL.count} 篇${accName ? ` · 发 ${accName}` : ""}`;
  const active = runs.filter(r => r.active);
  const recent = runs.filter(r => !r.active).slice(0, 4);
  const mini = r => `<div class="fl-mini" data-run="${r.id}" tabindex="0">${r.cover ? `<img src="${esc(thumb(r.cover, 240))}" alt="" loading="lazy" decoding="async">` : `<span class="fl-ph">${r.kind === "product" ? "产品" : "赛道"}</span>`}<div class="fl-rt"><b>${esc(r.name)}</b><span class="sub">${r.target} 篇 · ${when(r.created)}</span></div>${runPill(r)}</div>`;
  const aside = `<aside class="fl-aside">
    <section class="card"><div class="card-h"><b>正在进行</b><button class="btn ghost sm" data-go="runs" style="margin-left:auto">全部任务 ›</button></div>
      ${active.length ? active.slice(0, 4).map(mini).join("") : `<p class="sub" style="margin:0">没有进行中的任务</p>`}</section>
    ${recent.length ? `<section class="card"><div class="card-h"><b>最近完成</b></div>${recent.map(mini).join("")}</section>` : ""}
    <section class="card"><div class="card-h"><b>怎么用</b></div><ol class="fl-tips"><li>选已有产品或上传产品图,也可以按赛道生成</li><li>选打算发布的账号,系统按这个账号的数据推荐选题角度</li><li>点「开始生成」后可以离开,做完的笔记在「成品库」</li><li>发布后在「数据看板」看每篇的表现</li></ol></section>
  </aside>`;
  $("#main").innerHTML = `<div class="fl-newgrid"><section class="card fl-new">
    <h3 class="fl-sh"><i>1</i>做什么</h3>
    <div class="seg"><button class="${prod ? "on" : ""}" data-fmode="product">我有产品</button><button class="${prod ? "" : "on"}" data-fmode="track">按赛道</button></div>
    ${prod ? `${list.length ? `<span class="fl-lbl">已有产品</span><div class="fl-chips">${list.map(x => `<button class="chip ${P.pick === x.id ? "on" : ""}" data-fpick="${x.id}">${esc(x.name)}</button>`).join("")}${P.pick ? `<button class="chip" data-fpick="0">+ 新产品</button>` : ""}</div>` : ""}
      ${picked && FL.edit?.id === picked.id ? editForm(FL.edit)
      : picked ? `<div class="fl-ups">${picked.images.map((u, i) => imgTile("v", u, i, picked.images.length)).join("")}</div>
        <div class="fl-pinfo"><div><b>${esc(picked.name)}</b><p class="sub">${[picked.price, picked.selling_points && "卖点:" + picked.selling_points, picked.audience && "人群:" + picked.audience, picked.forbid && "不写:" + picked.forbid].filter(Boolean).map(esc).join(" · ") || "没有补充资料"}</p></div>
          <button class="btn gray sm" data-fedit>编辑</button><button class="btn ghost sm danger" data-fdel>${FL.delSure === picked.id ? "再点一次确认删除" : "删除"}</button></div>`
      : `<label class="fl-lbl" for="fpname">产品名称</label><input id="fpname" placeholder="如:国圣台湾风味豆腐乳" value="${esc(P.name)}">
        <span class="fl-lbl">产品图 · 点图片放大,拖动或点 ‹ › 调整顺序</span><div class="pc-tip"><b>怎么传才好用</b><ul>
        <li>建议 3–6 张:<b>① 包装正面</b>(白底最好)、<b>② 开盖 / 内容物特写</b>、<b>③ 1–2 张真实使用或食用场景</b></li>
        <li>一次只传<b>一个产品</b>,图里不要出现别的产品或别的品牌</li>
        <li>生图时只取<b>前 3 张</b>当参考(带数字角标的就是),把最能代表产品的放在最前面</li></ul></div>
        <div class="fl-ups">${P.files.map((u, i) => imgTile("p", u, i, P.files.length, `<button type="button" data-frm="${i}" aria-label="移除第 ${i + 1} 张">×</button>`)).join("")}<label class="fl-add">+ 上传<input id="ffile" type="file" accept="image/*" multiple hidden></label></div>
        <details class="fl-more"><summary>补充资料(可选,写得越准,文案越贴合)</summary><div class="pc-tip"><b>填写要点</b><ul>
          <li><b>卖点</b>:3–5 条短句,只写这个产品,不要复制别的产品的文案</li>
          <li><b>适合人群</b>:只写人群本身,如「上班族、控糖人群」;不要写给 AI 的指令(如「帮我收集…」)</li>
          <li><b>不能出现的说法</b>:如「治疗、功效」这类词,逗号隔开</li></ul>
          <details class="pc-eg"><summary>看一个填写范例</summary><p><b>名称</b>:国圣米酱豆腐乳<br><b>卖点</b>:米酱发酵,咸香入味 / 绵密细腻,不辣不刺激 / 配白粥、拌面、拌饭都合适<br>
          <b>适合人群</b>:家庭主妇、中老年人<br><b>不能出现的说法</b>:治疗、功效</p></details></div>
          <div class="fl-two"><input id="fpprice" placeholder="价格,如 29.9 元 / 罐" value="${esc(P.price)}"><input id="fpaud" placeholder="适合人群,只写人群,如 上班族、控糖人群" value="${esc(P.aud)}"></div>
          <input id="fpsp" placeholder="卖点,3–5 条短句,如 米酱发酵 · 绵密不齁 · 配饭配粥" value="${esc(P.sp)}"><input id="fpforbid" placeholder="不能出现的说法,如 不写疗效" value="${esc(P.forbid)}">
          <label class="sub">赛道 <select id="fptrack"><option value="">自动识别</option>${TRACKS.map(([t]) => `<option ${P.track === t ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label></details>`}`
    : `<span class="fl-lbl">赛道</span><div class="fl-chips">${TRACKS.map(([t]) => `<button class="chip ${t === FL.track ? "on" : ""}" data-ftrack="${esc(t)}">${esc(t)}</button>`).join("")}</div>
      <label class="fl-lbl" for="fkw">关键词(可选,不填由系统按赛道挑选)</label><input id="fkw" placeholder="如:快手早餐,多个用逗号分隔" value="${esc(FL.kw)}">
      ${FL.account ? `<div id="fakw">${accKwHtml(FL.accKw[FL.account])}</div>` : ""}
      ${(hot.keywords || []).length || (hot.topics || []).length ? `<span class="fl-lbl">最近的热词 · 点一下加进关键词</span><div class="fl-chips">${[...(hot.keywords || []).slice(0, 8), ...(hot.topics || []).slice(0, 6)].map(w => `<button class="chip" data-fhot="${esc(w)}">${esc(w)}</button>`).join("")}</div>` : ""}`}
    ${accts.length ? `<h3 class="fl-sh fl-sec"><i>2</i>发到哪个账号 <small>可不选</small></h3>` : ""}
    ${accts.length ? `<span class="fl-lbl">打算发到哪个账号 · 可不选,发布后按标题自动对上</span><div class="fl-chips"><button class="chip ${FL.account ? "" : "on"}" data-facc="">不指定</button>${accts.map(a => `<button class="chip ${FL.account === a.account_id ? "on" : ""}" data-facc="${esc(a.account_id)}" title="${esc(a.shop_name || "")}">${esc(a.name)}${new Set(accts.map(x => x.shop_name)).size > 1 && a.shop_name && a.shop_name.replace(/的店$/, "") !== a.name ? `<span class="sub"> · ${esc(a.shop_name.replace(/的店$/, ""))}</span>` : ""}</button>`).join("")}</div>` : ""}
    ${plan ? angleBlock(plan) : ""}
    <h3 class="fl-sh fl-sec"><i>${accts.length ? 3 : 2}</i>生成几篇</h3>
    <div class="fl-num">
      <div class="fl-step"><button data-fcnt="-1" aria-label="减少">−</button><b>${FL.count}</b><button data-fcnt="1" aria-label="增加">+</button></div>
      <div class="fl-chips">${[5, 10, 20].map(n => `<button class="chip ${FL.count === n ? "on" : ""}" data-fset="${n}">${n}</button>`).join("")}</div>
    </div>
    <span class="fl-lbl" title="系统会按类型分别挑参考笔记、分别拆解、按各自的写法写">写什么类型的帖子</span>
    <div class="fl-chips">${PTYPES.map(([k, n, tip]) => `<button class="chip ${(FL.ptype || "") === k ? "on" : ""}" data-fptype="${k}" title="${esc(tip)}">${n}</button>`).join("")}</div>
    <div class="fl-foot fl-startbar">
      <span class="fl-sum" title="${esc(sum)}">${esc(sum)}</span>
      <span class="fl-err" id="ferr" role="alert"></span>
      <button class="btn lg" id="fstart">开始生成</button>
    </div>
  </section>${aside}</div>`;
  if (FL.account && !prod && FL.accKw[FL.account] === undefined) {   // AI 推荐搜索词要几秒:先渲染页面,回来再填进占位处,不重绘整页(免得冲掉正在输入的内容)
    const acc = FL.account;
    api(`/api/qianfan/account_keywords?account_id=${encodeURIComponent(acc)}`).then(r => r.keywords || []).catch(() => []).then(ks => {
      FL.accKw[acc] = ks; if ($("#fakw") && FL.account === acc) $("#fakw").innerHTML = accKwHtml(ks);
    });
  }
  const keep = () => {
    if ($("#fkw")) FL.kw = $("#fkw").value;
    for (const [k, id] of [["name", "fpname"], ["price", "fpprice"], ["aud", "fpaud"], ["sp", "fpsp"], ["forbid", "fpforbid"], ["track", "fptrack"]]) if ($("#" + id)) P[k] = $("#" + id).value;
  };
  $("#main").onclick = e => {
    const t = e.target;
    const pt = t.closest("[data-fptype]"); if (pt) { keep(); FL.ptype = pt.dataset.fptype; return fNew(); }
    const m = t.closest("[data-fmode]"); if (m) { keep(); FL.mode = m.dataset.fmode; return fNew(); }
    const pk = t.closest("[data-fpick]"); if (pk) { keep(); P.pick = +pk.dataset.fpick; FL.edit = null; FL.delSure = 0; return fNew(); }
    if (t.closest("[data-fedit]")) { FL.edit = { id: picked.id, name: picked.name, price: picked.price || "", sp: picked.selling_points || "", aud: picked.audience || "", forbid: picked.forbid || "", imgs: picked.images.map(u => ({ u })) }; return fNew(); }
    if (t.closest("[data-fecancel]")) { FL.edit = null; return fNew(); }
    const er = t.closest("[data-fermv]"); if (er) { keepEdit(); FL.edit.imgs.splice(+er.dataset.fermv, 1); return fNew(); }
    const zm = t.closest("[data-zoom]"); if (zm) { const [k, i] = zm.dataset.zoom.split(":"); return lightbox(imgList(k), +i); }
    const mvb = t.closest("[data-mv]"); if (mvb) { const [k, i, d] = mvb.dataset.mv.split(":"); return moveImg(k, +i, +i + +d); }
    if (t.closest("[data-fesave]")) return saveEdit();
    if (t.closest("[data-fdel]")) {
      if (FL.delSure !== picked.id) { FL.delSure = picked.id; return fNew(); }
      return api(`/api/products/${picked.id}`, "DELETE").then(() => { toast("产品已删除"); P.pick = 0; FL.delSure = 0; fNew(); }).catch(e => { FL.delSure = 0; toast(e.message, 1); fNew(); });
    }
    const rm = t.closest("[data-frm]"); if (rm) { keep(); P.files.splice(+rm.dataset.frm, 1); return fNew(); }
    const ag = t.closest("[data-fang]"); if (ag) { keep(); const n = ag.dataset.fang; FL.angles.has(n) ? FL.angles.delete(n) : FL.angles.add(n); return fNew(); }
    const ac = t.closest("[data-facc]"); if (ac) { keep(); FL.account = ac.dataset.facc; return fNew(); }
    const tr = t.closest("[data-ftrack]"); if (tr) { keep(); FL.track = tr.dataset.ftrack; return fNew(); }
    const c = t.closest("[data-fcnt]"); if (c) { keep(); FL.count = Math.max(1, Math.min(50, FL.count + +c.dataset.fcnt)); return fNew(); }
    const st = t.closest("[data-fset]"); if (st) { keep(); FL.count = +st.dataset.fset; return fNew(); }
    const rr = t.closest("[data-rerun]"); if (rr) return rerunRun(rr);
    const hw = t.closest("[data-fhot]"); if (hw) { keep(); const w = hw.dataset.fhot, cur = FL.kw.split(/[,，、\n]+/).map(s => s.trim()).filter(Boolean); if (!cur.includes(w)) cur.push(w); FL.kw = cur.join("、"); return fNew(); }
    const r = t.closest("[data-run]"); if (r) return openRun(+r.dataset.run);
  };
  // 图片列表和调整顺序:p = 新产品的图,e = 编辑中的图,v = 已选产品(只看)
  const imgList = k => k === "p" ? P.files : k === "e" ? (FL.edit?.imgs || []).map(x => x.u) : (picked?.images || []);
  const moveImg = (k, from, to) => {
    const arr = k === "p" ? P.files : k === "e" ? FL.edit?.imgs : null;
    if (!arr || to < 0 || to >= arr.length || from === to) return;
    if (k === "p") keep(); else keepEdit();
    arr.splice(to, 0, arr.splice(from, 1)[0]);
    fNew();
  };
  const ordOf = e => e.target.closest?.(".fl-up[data-ord]");
  $("#main").ondragstart = e => { const t = ordOf(e); if (!t) return; e.dataTransfer.setData("text/plain", t.dataset.ord); e.dataTransfer.effectAllowed = "move"; t.classList.add("drag"); };
  $("#main").ondragend = () => $$(".fl-up.drag, .fl-up.over").forEach(x => x.classList.remove("drag", "over"));
  $("#main").ondragover = e => { const t = ordOf(e); if (t) { e.preventDefault(); $$(".fl-up.over").forEach(x => x !== t && x.classList.remove("over")); t.classList.add("over"); } };
  $("#main").ondrop = e => {
    const t = ordOf(e); if (!t) return; e.preventDefault();
    const [k1, i1] = (e.dataTransfer.getData("text/plain") || ":").split(":"), [k2, i2] = t.dataset.ord.split(":");
    if (k1 === k2 && i1 !== "" ) moveImg(k1, +i1, +i2);
  };
  $("#main").onkeydown = e => { if ((e.key === "Enter" || e.key === " ") && e.target.matches?.("img[data-zoom]")) { e.preventDefault(); e.target.click(); } };
  const keepEdit = () => { const E = FL.edit; if (!E) return; for (const [k, id] of [["name", "fename"], ["price", "feprice"], ["aud", "feaud"], ["sp", "fesp"], ["forbid", "feforbid"]]) if ($("#" + id)) E[k] = $("#" + id).value; };
  const saveEdit = async () => {
    keepEdit(); const E = FL.edit;
    if (!E.name.trim()) return toast("产品名称不能为空", 1);
    if (!E.imgs.length) return toast("至少保留一张产品图", 1);
    const part = E_ => { const add = E_.imgs.filter(x => x.a), keepN = E_.imgs.filter(x => !x.a).map(x => x.u.split("/").pop());
      let ai = 0; return { images: add.map(x => x.u), keep: keepN, order: E_.imgs.map(x => x.a ? "a:" + ai++ : "k:" + x.u.split("/").pop()) }; };
    const pp = part(E);
    const btn_ = $("[data-fesave]"); btn_ && (btn_.disabled = true, btn_.textContent = "检查中…");
    const go_ = await checkProduct({ name: E.name, images: pp.images, keep: pp.keep, order: pp.order, selling_points: E.sp, audience: E.aud, forbid: E.forbid, product_id: E.id });
    btn_ && (btn_.disabled = false, btn_.textContent = "保存产品");
    if (!go_) return;
    try {
      await api(`/api/products/${E.id}`, "PUT", { name: E.name, price: E.price, selling_points: E.sp, audience: E.aud, forbid: E.forbid, keep: pp.keep, add: pp.images, order: pp.order });
      FL.edit = null; toast("产品已保存"); fNew();
    } catch (e) { toast(e.message, 1); }
  };
  $("#fefile") && ($("#fefile").onchange = async e => {
    keepEdit(); const E = FL.edit;
    try { for (const f of [...e.target.files]) if (E.imgs.length < 9) E.imgs.push({ u: await shrink(f), a: true }); } catch (err) { toast(err.message, 1); }
    fNew();
  });
  $("#ffile") && ($("#ffile").onchange = async e => {
    keep();
    try { for (const f of [...e.target.files]) if (P.files.length < 9) P.files.push(await shrink(f)); } catch (err) { toast(err.message, 1); }
    fNew();
  });
  $("#fstart").onclick = async () => {
    keep(); const err = m => { $("#ferr").textContent = m; };
    let body = { count: FL.count, ...(PTYPE_MIX[FL.ptype || ""] ? { mix: PTYPE_MIX[FL.ptype] } : {}), ...(FL.account ? { account_id: FL.account, angles: [...FL.angles] } : {}) };
    if (prod) {
      if (!picked && !P.name.trim()) return err("填一下产品名称");
      if (!picked && !P.files.length) return err("至少上传一张产品图");
      body = { ...body, track: P.track, ...(picked ? { product_id: picked.id } : { product: { name: P.name, images: P.files, price: P.price, selling_points: P.sp, audience: P.aud, forbid: P.forbid } }) };
    } else {
      if (!FL.track) return err("先选一个赛道");
      body = { ...body, track: FL.track, keywords: FL.kw.split(/[,，、\n]+/).map(s => s.trim()).filter(Boolean) };
    }
    if (prod && !picked) {   // 新产品:先检查素材(传错图、混进别的产品、文案像指令…),有问题让用户选继续还是回去改
      $("#fstart").disabled = true; $("#fstart").textContent = "检查素材中…";
      const go_ = await checkProduct({ name: P.name, images: P.files, selling_points: P.sp, audience: P.aud, forbid: P.forbid });
      if (!go_) { $("#fstart").disabled = false; $("#fstart").textContent = "开始生成"; return; }
    }
    $("#fstart").disabled = true; $("#fstart").textContent = prod && !picked ? "上传中…" : "创建中…";
    try {
      const r = await api("/api/auto", "POST", body);
      FL.P = { name: "", files: [], price: "", sp: "", aud: "", forbid: "", track: "", pick: 0 }; FL.kw = "";
      setProject(r.id); toast("已开始,流水线会自动跑完"); openRun(r.id);
    } catch (e) { err(e.message); $("#fstart").disabled = false; $("#fstart").textContent = "开始生成"; }
  };
}

// 帖子类型预设:空 = 系统默认(有产品:软文为主加测评;按赛道:软文 + 教程 + 测评)
const PTYPES = [["", "系统默认", "有产品:产品软文为主,加一部分测评;按赛道:软文、教程、测评混合"], ["soft", "产品软文", "用户视角的体验、卖点、观点分享"], ["tut", "教程", "配方、用量、步骤"],
  ["rev", "测评", "有评价维度的单款 / 多款对比"], ["biz", "商业硬文", "品牌 / 商家视角的产品介绍(参考笔记较少)"], ["mix", "软文 + 教程 + 测评", "三类混合,按 5:3:2 分配"]];
const PTYPE_MIX = { soft: { 产品软文: 1 }, tut: { 教程: 1 }, rev: { 测评: 1 }, biz: { 商业硬文: 1 }, mix: { 产品软文: 5, 教程: 3, 测评: 2 } };

// 提交产品前检查素材(传错图、混进别的产品的图、文案像指令 / 复制了别的产品的、太长被截断…)。
// 只提醒不拦着:有需要注意的问题时弹出清单,用户选「回去修改」或「仍然继续」;只有建议时不打断;检查本身出错也直接放行。
async function checkProduct(body) {
  let ws = [];
  try { ws = (await api("/api/products/check", "POST", body)).warnings || []; } catch { return true; }
  const warn = ws.filter(w => w.level === "warn");
  if (!warn.length) { if (ws[0]) toast("小建议:" + ws[0].msg); return true; }
  return new Promise(done => {
    const m = document.createElement("div"); m.className = "mask"; m.style.zIndex = 60;
    m.innerHTML = `<div class="card pc-dlg" role="dialog" aria-modal="true" aria-label="素材检查">
      <div class="card-h"><b>素材检查:有 ${warn.length} 个地方建议先看一下</b></div>
      <p class="sub" style="margin:0 0 8px">这些素材会直接影响生成的文案和配图(传错图、混进别的产品,生成的图里就会出现别的产品)。可以回去改,也可以确认没问题后继续。</p>
      <ul class="pc-list">${ws.map(w => `<li class="${w.level}"><span class="pc-t">${esc(w.where)}</span>${esc(w.msg)}</li>`).join("")}</ul>
      <div class="fl-acts" style="justify-content:flex-end;margin-top:14px"><button class="btn gray" data-pc="back">回去修改</button><button class="btn" data-pc="go">仍然继续</button></div></div>`;
    const end = r => { m.remove(); document.removeEventListener("keydown", key); done(r); };
    const key = e => { if (e.key === "Escape") end(false); };
    m.onclick = e => { const b = e.target.closest("[data-pc]"); if (b) end(b.dataset.pc === "go"); else if (e.target === m) end(false); };
    document.addEventListener("keydown", key); document.body.appendChild(m); m.querySelector('[data-pc="back"]').focus();
  });
}

function editForm(E) {
  return `<div class="fl-edit"><label class="fl-lbl" for="fename">产品名称</label><input id="fename" value="${esc(E.name)}">
    <span class="fl-lbl">产品图 · 点图片放大,拖动或点 ‹ › 调整顺序,点 × 去掉(至少留一张)</span><div class="pc-tip"><b>怎么传才好用</b><ul>
        <li>建议 3–6 张:<b>① 包装正面</b>(白底最好)、<b>② 开盖 / 内容物特写</b>、<b>③ 1–2 张真实使用或食用场景</b></li>
        <li>一次只传<b>一个产品</b>,图里不要出现别的产品或别的品牌</li>
        <li>生图时只取<b>前 3 张</b>当参考(带数字角标的就是),把最能代表产品的放在最前面</li></ul></div>
    <div class="fl-ups">${E.imgs.map((x, i) => imgTile("e", x.u, i, E.imgs.length, `<button type="button" data-fermv="${i}" aria-label="去掉这张">×</button>`)).join("")}<label class="fl-add">+ 添加<input id="fefile" type="file" accept="image/*" multiple hidden></label></div>
    <div class="fl-two"><input id="feprice" placeholder="价格,如 29.9 元 / 罐" value="${esc(E.price)}"><input id="feaud" placeholder="适合人群,如 上班族" value="${esc(E.aud)}"></div>
    <input id="fesp" placeholder="卖点,如 古法发酵 · 小罐装" value="${esc(E.sp)}" style="margin-top:10px"><input id="feforbid" placeholder="不能出现的说法,如 不写疗效" value="${esc(E.forbid)}" style="margin-top:10px">
    <div class="fl-acts" style="margin-top:12px;justify-content:flex-end"><button class="btn gray sm" data-fecancel>取消</button><button class="btn sm" data-fesave>保存产品</button></div></div>`;
}

// 用同样的设置再跑一次:新建一个任务,原来的保留(产品任务会重新识别产品图)
async function rerunRun(btn) {
  btn.disabled = true; const old = btn.textContent; btn.textContent = "创建中…";
  try {
    const r = await api(`/api/runs/${btn.dataset.rerun}/rerun`, "POST");
    setProject(r.id); toast("已用同样的设置重新开始"); openRun(r.id);
  } catch (e) { toast(e.message, 1); btn.disabled = false; btn.textContent = old; }
}

// ---------- 任务 ----------
const openRun = id => { state.runId = id; localStorage.runId = id; go("run"); };
const RUN_SIZE = 24;   // 任务页每页个数
async function fRuns() {
  const draw = (rs, total) => {
    const pages = Math.max(1, Math.ceil(total / RUN_SIZE));
    const grp = (title, cls, list) => list.length ? `<div class="fl-gh ${cls}"><b>${title}</b><span>${list.length}</span></div><div class="fl-runs">${list.map(runCard).join("")}</div>` : "";
    const act = rs.filter(r => r.active), bad = rs.filter(r => !r.active && r.status === "error"), rest = rs.filter(r => !r.active && r.status !== "error");
    $("#main").innerHTML = rs.length ? `${grp("进行中", "run", act)}${grp("出错,需要处理", "bad", bad)}${grp(act.length || bad.length ? "已完成" : "全部任务", "ok", rest)}${pager(FL.runsPage, total, RUN_SIZE)}`
      : `<div class="empty"><b>还没有任务</b>选赛道或上传产品,填好篇数就能开始<br><button class="btn" data-go="new">新建任务</button></div>`;
    FL.sig = JSON.stringify(rs); FL.runsPages = pages;
  };
  const load = async () => {
    const { total } = await api("/api/runs/count");
    FL.runsPage = Math.min(Math.max(1, FL.runsPage || 1), Math.max(1, Math.ceil(total / RUN_SIZE)));   // 任务被删后页数变少,落到最后一页
    return [await api(`/api/runs?limit=${RUN_SIZE}&offset=${(FL.runsPage - 1) * RUN_SIZE}`), total];
  };
  let [rs, total] = await load(); draw(rs, total);
  $("#main").onclick = e => {
    const g = e.target.closest("[data-pg]"); if (g) { if (g.disabled) return; FL.runsPage = +g.dataset.pg; return fRuns().then(() => window.scrollTo({ top: 0, behavior: "smooth" })); }
    const rd = e.target.closest("[data-rdel]");   // 删除任务:点两次(不用浏览器自带的确认框),5 秒内不再点就还原
    if (rd) {
      const id = +rd.dataset.rdel;
      if (FL.delRun !== id) { FL.delRun = id; clearTimeout(FL.delRunT); FL.delRunT = setTimeout(() => { FL.delRun = 0; if (state.tab === "runs") draw(rs, total); }, 5000); return draw(rs, total); }
      FL.delRun = 0; rd.closest(".fl-run")?.remove(); rs = rs.filter(r => r.id !== id); total--;
      return api(`/api/runs/${id}`, "DELETE").then(() => toast("任务已删除")).catch(err => toast(err.message, 1)).finally(() => fRuns());
    }
    const rr = e.target.closest("[data-rerun]"); if (rr) return rerunRun(rr); const r = e.target.closest("[data-run]"); if (r) openRun(+r.dataset.run);
  };
  if (rs.some(r => r.active)) loop(async () => { const [n, t] = await load(); if (JSON.stringify(n) !== FL.sig) draw(n, t); return n.some(r => r.active); }, 4000);
}
function stepNote(r, s) {
  return { "识别产品": r.category ? `识别为「${esc(r.category)}」` : "", "采集": `${r.library ? `笔记库复用 ${r.library} 篇` + (r.tasks[1] ? " · " : " · 跳过采集") : ""}${r.tasks[1] ? `${r.tasks[0]}/${r.tasks[1]} 个关键词` : ""} · 共 ${r.notes} 篇<br><span class="sub">${esc(r.keywords.join("、"))}</span>`,
    "分类": r.notes ? `相关笔记 ${r.relevant} 篇` : "", "拆解": r.playbooks[1] ? `${r.playbooks[0]}/${r.playbooks[1]} 张拆解卡 · 参考 ${r.refs} 篇${r.reused ? `(${r.reused} 篇以前用过)` : ""}` : "",
    "写稿配图": r.drafts.total ? `${r.drafts.finished || 0}/${r.drafts.total} 篇` : "", "质检": r.status === "done" ? `合格 ${r.drafts.passed} · 需检查 ${r.drafts.failed}` : "" }[s] || "";
}
async function fRun() {
  if (!state.runId) return go("runs");
  let r;
  try { r = await api(`/api/runs/${state.runId}`); } catch { return go("runs"); }
  const ds = r.drafts.total ? await api(`/api/library?pid=${r.id}&brief=1`) : [];
  $("#pt").textContent = r.name;
  if (FL.runPageId !== r.id) { FL.runPageId = r.id; FL.runPage = 1; }   // 换了任务从第 1 页看起
  const runPage = Math.min(Math.max(1, FL.runPage || 1), Math.max(1, Math.ceil(ds.length / LIB_SIZE)));
  $("#main").innerHTML = `<button class="btn ghost sm fl-back" data-go="runs">‹ 全部任务</button>
  <section class="card fl-detail">
    <div class="fl-rh"><div class="fl-rt"><b>${esc(r.name)}</b><span class="sub">${r.kind === "product" ? "产品" : "赛道"} · ${esc(r.track)} · ${r.target} 篇 · ${when(r.created)}</span></div>${runPill(r)}</div>
    ${r.status === "error" ? `<div class="banner err">${esc(r.msg)}</div>` : r.active ? `<p class="sub fl-msg">${esc(r.msg)}</p>` : ""}
    <ol class="fl-tl">${r.steps.map((s, i) => `<li class="${segCls(r, i) || "todo"}"><span class="dot"></span><b>${s}</b><span class="fl-sn">${i <= r.step ? stepNote(r, s) : ""}</span></li>`).join("")}</ol>
    <div class="fl-acts">${r.active ? `<span class="sub">可以离开这个页面,做完会出现在「成品库」</span><button class="btn ghost sm danger" id="fcancel">取消任务</button>` : ""}
      ${r.active ? "" : `<button class="btn" data-rerun="${r.id}">重新运行</button>`}
</div>
  </section>
  ${ds.length ? `<div class="fl-h"><b>成品 ${ds.length} 篇</b>${!r.active ? `<button class="btn ghost sm" id="fgolib">在成品库中筛选 ›</button>` : ""}</div><div class="fl-grid">${ds.slice((runPage - 1) * LIB_SIZE, runPage * LIB_SIZE).map(noteCard).join("")}</div>${pager(runPage, ds.length, LIB_SIZE)}` : ""}`;
  $("#main").onclick = e => { const g = e.target.closest("[data-pg]"); if (g) { if (g.disabled) return; FL.runPage = +g.dataset.pg; return fRun().then(() => window.scrollTo({ top: 0, behavior: "smooth" })); }
    const rr = e.target.closest("[data-rerun]"); if (rr) return rerunRun(rr); const n = e.target.closest("[data-note]"); if (n) fOpen(+n.dataset.note, ds); };
  $("#fgolib") && ($("#fgolib").onclick = () => { FL.pid = r.id; FL.filter = "all"; go("lib"); });
  $("#fcancel") && ($("#fcancel").onclick = async () => {
    if (!confirm("取消后已生成的稿子会保留,没跑完的步骤不再继续。确定取消?")) return;
    await api(`/api/projects/${r.id}/auto/cancel`, "POST"); toast("任务已取消"); fRun();
  });
  if (r.active) { const sig = JSON.stringify(r); loop(async () => { const n = await api(`/api/runs/${r.id}`); if (JSON.stringify(n) !== sig) { fRun(); return false; } return true; }, 4000); }
}

// ---------- 成品库 ----------
const LIB_SIZE = 36;   // 成品库每页篇数
async function fLib() {
  const [all, runs] = await Promise.all([api(`/api/library?brief=1${FL.pid ? `&pid=${FL.pid}` : ""}`), api("/api/runs?limit=200").catch(() => [])]);
  const by = { all: () => true, todo: d => !d.published, ok: d => d.auto_ok === 1, warn: d => d.auto_ok === 0 };
  const accOpts = [...new Map(all.filter(d => d.account_id).map(d => [d.account_id, d.account_name])).entries()];
  if (FL.acc && FL.acc !== "_none" && !accOpts.some(([id]) => id === FL.acc)) FL.acc = "";
  const byAcc = d => !FL.acc || (FL.acc === "_none" ? !d.account_id : d.account_id === FL.acc);
  const ds = all.filter(by[FL.filter] || by.all).filter(byAcc);
  const libPages = Math.max(1, Math.ceil(ds.length / LIB_SIZE)), libPage = Math.min(Math.max(1, FL.libPage || 1), libPages);   // 筛选后页数变少时,自动落到最后一页
  document.body.classList.toggle("fl-picking", FL.picking);
  $("#pa").innerHTML = FL.picking ? "" : `<button class="btn gray" id="fpon">批量导出</button>`;
  $("#main").innerHTML = `<div id="fres"></div><div class="fl-bar">
    <div class="fl-chips">${[["all", "全部"], ["todo", "未发布"], ["ok", "合格"], ["warn", "需检查"]].map(([k, l]) => `<button class="chip ${FL.filter === k ? "on" : ""}" data-ff="${k}">${l} ${all.filter(by[k]).length}</button>`).join("")}</div>
    <select id="frun" aria-label="按任务筛选"><option value="0">全部任务</option>${runs.map(r => `<option value="${r.id}" ${FL.pid === r.id ? "selected" : ""}>${esc(r.name)}</option>`).join("")}</select>
    ${accOpts.length ? `<select id="facc2" aria-label="按发布账号筛选"><option value="">全部账号</option>${accOpts.map(([id, n]) => `<option value="${esc(id)}" ${FL.acc === id ? "selected" : ""}>${esc(n)}</option>`).join("")}<option value="_none" ${FL.acc === "_none" ? "selected" : ""}>未指定账号</option></select>` : ""}</div>
  ${FL.picking ? `<div class="fl-pickbar" role="toolbar" aria-label="批量导出"><span>已选 <b>${FL.picked.size}</b> 篇</span><button class="btn gray sm" id="fpall">全选当前 ${ds.length} 篇</button><button class="btn gray sm" id="fpoff">取消</button><button class="btn" id="fzip">打包 ${FL.picked.size} 篇</button></div>` : ""}
  ${ds.length ? `<div class="pg-top"><span class="sub">共 ${ds.length} 篇 · 第 ${(libPage - 1) * LIB_SIZE + 1}–${Math.min(libPage * LIB_SIZE, ds.length)} 篇${libPages > 1 ? ` · 第 ${libPage} / ${libPages} 页` : ""}</span></div><div class="fl-grid">${ds.slice((libPage - 1) * LIB_SIZE, libPage * LIB_SIZE).map(noteCard).join("")}</div>${pager(libPage, ds.length, LIB_SIZE)}` : `<div class="empty"><b>${all.length ? "这个筛选下没有笔记" : "还没有成品"}</b>${all.length ? "换个筛选看看" : "新建一个任务,做完的笔记会出现在这里"}${all.length ? "" : `<br><button class="btn" data-go="new">新建任务</button>`}</div>`}`;
  $("#main").onclick = e => {
    const f = e.target.closest("[data-ff]"); if (f) { FL.filter = f.dataset.ff; FL.libPage = 1; return fLib(); }
    const g = e.target.closest("[data-pg]");
    if (g) { if (g.disabled) return; FL.libPage = +g.dataset.pg; return fLib().then(() => window.scrollTo({ top: 0, behavior: "smooth" })); }
    const n = e.target.closest("[data-note]"); if (!n) return;
    const id = +n.dataset.note;
    if (FL.picking) { FL.picked.has(id) ? FL.picked.delete(id) : FL.picked.add(id); return fLib(); }
    fOpen(id, ds);
  };
  $("#frun").onchange = e => { FL.pid = +e.target.value; FL.libPage = 1; fLib(); };
  $("#facc2") && ($("#facc2").onchange = e => { FL.acc = e.target.value; FL.libPage = 1; fLib(); });
  $("#fpon") && ($("#fpon").onclick = () => { FL.picking = true; FL.picked = new Set(ds.filter(d => d.auto_ok !== 0 && !d.published).map(d => d.id)); fLib(); });
  $("#fpoff") && ($("#fpoff").onclick = () => { FL.picking = false; FL.picked.clear(); fLib(); });
  $("#fpall") && ($("#fpall").onclick = () => { ds.forEach(d => FL.picked.add(d.id)); fLib(); });
  $("#fzip") && ($("#fzip").onclick = async () => {
    const ids = [...FL.picked]; if (!ids.length) return toast("先点选要导出的笔记", 1);
    const bad = all.filter(d => FL.picked.has(d.id) && (d.high || d.holes));
    if (bad.length && !confirm(`有 ${bad.length} 篇还有高风险词或待补充的内容,发布前需要处理。仍然导出?`)) return;
    const btn = $("#fzip"), setBtn = t => { btn.innerHTML = `<span class="fl-spin" aria-hidden="true"></span>${t}`; };
    btn.disabled = true; setBtn("准备导出…"); $$("#fpall, #fpoff").forEach(b => b.disabled = true);
    const prog = s => {   // 顶部进度条 + 按钮上的进度,一眼看得出在导出、进行到哪了
      const pct = s.stage === "zip" ? 96 : s.total ? Math.round(s.done / s.total * 90) : 0;
      $("#fres").innerHTML = `<div class="banner fl-prog" role="status"><div style="flex:1;min-width:0"><b>${s.stage === "zip" ? "正在压缩…" : `正在导出 ${s.done}/${s.total} 篇`}</b>
        <span class="sub" style="margin-left:8px">图片较多时需要几十秒,请不要关闭页面或重复点击</span><div class="fl-pbar"><i style="width:${pct}%"></i></div></div></div>`;
      setBtn(s.stage === "zip" ? "正在压缩…" : `正在导出 ${s.done}/${s.total}`);
    };
    prog({ done: 0, total: ids.length, stage: "copy" }); window.scrollTo({ top: 0 });
    try {
      const { job } = await api("/api/export/start", "POST", { ids });
      let s;
      for (;;) {
        await new Promise(r => setTimeout(r, 600));
        s = await api(`/api/export/${job}`);
        if (s.state === "error") throw new Error(s.error || "导出失败");
        if (s.state === "done") break;
        prog(s);
      }
      const zip = s.zip;
      FL.picking = false; FL.picked.clear(); await fLib();
      const url = `/download/${encodeURIComponent(zip)}`;
      $("#fres").innerHTML = `<div class="banner ok">已打包 ${ids.length} 篇:<a href="${url}" download>下载 ${esc(zip)}</a><span class="sub" style="color:inherit">(没有自动开始下载就点这个链接)</span></div>`;
      window.scrollTo({ top: 0 }); toast("已打包,下载已开始");
      const a = document.createElement("a"); a.href = url; a.download = zip; document.body.appendChild(a); a.click(); a.remove();   // 自动开始下载
    } catch (e) { $("#fres").innerHTML = `<div class="banner err">打包失败:${esc(e.message)}</div>`; btn.disabled = false; btn.textContent = `打包 ${FL.picked.size} 篇`; $$("#fpall, #fpoff").forEach(b => b.disabled = false); }
  });
}

// ---------- 笔记预览:左图右文,手机全屏,左右滑切换 ----------
// 列表只带精简字段(没有正文),预览要的完整内容按需取;取到后并回列表里的那一项
const pvFull = async d => { if (d.content !== undefined) return d; try { Object.assign(d, await api(`/api/library/${d.id}`)); } catch { d.content = d.content ?? ""; d.tags = d.tags || []; } return d; };
async function fOpen(id, list) {
  await fetchAccts();
  FL.pv = { list, i: list.findIndex(d => d.id === id), img: 0, dirty: false, more: false, opener: document.activeElement };
  document.body.classList.add("fl-lock"); await pvFull(list[FL.pv.i]); if (FL.pv) drawPv();
}
function closePv() { const dirty = FL.pv?.dirty, op = FL.pv?.opener; FL.pv = null; $(".fl-pv")?.remove(); document.body.classList.remove("fl-lock"); if (dirty) render(); else if (op && op.isConnected) op.focus({ preventScroll: true }); }
// 成品预览里的发布登记:选发布日期登记;已登记但没对上千帆数据时,可以手动对上;对上的可以取消
const todayStr = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
function pubPanel(d) {
  if (!d.published) return `<div class="pv-pub"><b>发布登记</b><span class="sub">发到小红书后在这里登记,复盘页才会统计它的数据</span><div class="pv-pr"><label class="sub">发布日期 <input type="date" id="pvday" value="${todayStr()}" max="${todayStr()}" style="width:auto"></label><button class="btn sm" data-ppubd>登记已发布</button></div></div>`;
  return `<div class="pv-pub"><b>发布登记</b><span class="sub">已登记发布${d.published_at ? ` · ${esc(d.published_at)}` : ""}${d.linked ? " · 已手动对上千帆笔记" : d.perf ? " · 已自动对上千帆笔记" : ""}</span>
    <div class="pv-pr">${d.perf ? "" : `<button class="btn gray sm" data-plink>对上千帆笔记</button>`}${d.linked ? `<button class="btn ghost sm" data-punlink>取消对应</button>` : ""}<label class="sub">发布日期 <input type="date" id="pvday" value="${esc(d.published_at || todayStr())}" max="${todayStr()}" style="width:auto"></label><button class="btn gray sm" data-ppubd>修改日期</button></div></div>`;
}
const pvRefresh = async d => { const fresh = await api(`/api/library/${d.id}`).catch(() => null); if (fresh) Object.assign(d, fresh); FL.pv && (FL.pv.dirty = true); if (FL.pv) drawPv(); };
function drawPv() {
  const v = FL.pv, d = v.list[v.i], N = v.list.length, imgs = d.images;
  let m = $(".fl-pv"), fresh = !m; if (!m) { m = document.createElement("div"); m.className = "fl-pv"; document.body.appendChild(m); bindPv(m); }
  m.innerHTML = `<div class="fl-sheet" role="dialog" aria-modal="true" aria-label="笔记预览">
    <div class="fl-gal"><button class="fl-xm" data-pclose aria-label="关闭">×</button>${imgs.length ? `<div class="fl-big"><img src="/images/${esc(imgs[v.img])}" alt="第 ${v.img + 1} 张配图">${imgs.length > 1 ? `<button class="fl-ib l" data-pimg="-1" aria-label="上一张">‹</button><button class="fl-ib r" data-pimg="1" aria-label="下一张">›</button>` : ""}</div>
      <div class="fl-thumbs">${imgs.map((u, k) => `<button class="${k === v.img ? "on" : ""}" data-pto="${k}" aria-label="第 ${k + 1} 张"><img src="${esc(thumb("/images/" + u, 240))}" alt=""></button>`).join("")}</div>
      <p class="fl-hint">手机上长按图片可保存到相册</p>` : `<div class="fl-big">${noteCover(d, true)}</div>`}</div>
    <div class="fl-txt">
      <div class="fl-th"><span class="fl-badge ${d.auto_ok === 0 ? "warn" : "ok"}">${d.auto_ok === 0 ? "需检查" : d.auto_ok === 1 ? "合格" : "成品"}</span>${d.score != null ? `<span class="sub">审稿 ${d.score} 分</span>` : ""}${d.ptype ? `<span class="fl-badge ${d.ptype_got && d.ptype_got !== d.ptype ? "warn" : "ok"}" title="${esc(d.ptype_got && d.ptype_got !== d.ptype ? `目标是${d.ptype},写完后判成了${d.ptype_got}` : d.ptype_got ? "写完后复判,类型一致" : "目标类型")}">${esc(d.ptype)}${d.ptype_got && d.ptype_got !== d.ptype ? ` → ${esc(d.ptype_got)}` : ""}</span>` : ""}
        <span class="sub fl-cnt">${v.i + 1} / ${N}</span><button class="btn gray sm" data-pstep="-1" aria-label="上一篇">‹</button><button class="btn gray sm" data-pstep="1" aria-label="下一篇">›</button><button class="fl-x" data-pclose aria-label="关闭">×</button></div>
      <div class="fl-tb">${d.auto_ok === 0 ? `<div class="banner warn">需检查:${esc(d.auto_note)} <button class="btn ghost sm" data-pedit>去修改</button></div>` : ""}
        <h2>${esc(d.title)}</h2><div class="fl-body">${esc(d.content)}</div><div class="fl-tags">${d.tags.map(t => "#" + esc(t)).join(" ")}</div>
        <p class="sub">${esc(d.run)} · #${d.id}${d.published ? " · 已发布" : ""}</p>
        <section class="pv-box"><h4>发布</h4>
        <div class="fl-acct"><label class="sub">发布账号 <select data-pacc>${`<option value="">未指定</option>` + (FL.accts || []).map(a => `<option value="${esc(a.account_id)}" ${d.account_id === a.account_id ? "selected" : ""}>${esc(a.name)}</option>`).join("")}</select></label></div>
        ${pubPanel(d)}
        ${d.perf ? `<div class="banner ok" style="margin-top:10px"><b>已在千帆里对上号</b> · 发在「${esc(d.perf.account_name || "未知账号")}」${d.perf.published_at ? ` · ${esc(String(d.perf.published_at).slice(0, 10))}` : ""}${d.account_id && d.perf.account_id && d.account_id !== d.perf.account_id ? ` <span class="warn">(和指定的账号不一致)</span>` : ""}<br><span class="sub" style="color:inherit">阅读 ${fmtN(d.perf.views)} · 点赞 ${fmtN(d.perf.likes)} · 收藏 ${fmtN(d.perf.collects)} · 评论 ${fmtN(d.perf.comments)}${d.perf.shares != null ? ` · 分享 ${fmtN(d.perf.shares)}` : ""}</span></div>` : d.published ? `<p class="sub">已标记发布,千帆数据同步后会自动对上号并显示表现</p>` : ""}</section></div>
      <div class="fl-tf">
        <button class="btn gray" data-pcopy="all">复制全文</button>
        ${imgs.length ? `<a class="btn gray" href="/api/drafts/${d.id}/images.zip">下载图片</a>` : ""}
        <button class="btn gray" data-pmore aria-expanded="${!!v.more}">更多 ${v.more ? "▴" : "▾"}</button>
        <button class="btn ${d.published ? "gray" : ""}" data-ppub>${d.published ? "已发布 ✓" : "标记已发布"}</button>
        ${v.more ? `<div class="fl-tf2"><button class="btn gray" data-pcopy="title">复制标题</button>
          ${d.can_cover ? `<button class="btn gray" data-pcover>换封面</button>` : ""}
          ${imgs.length ? `<button class="btn gray" data-plive title="把当前这张图做成 iPhone 实况照片(Live 图),由 Mac 端生成">做成 Live 图</button>` : ""}
          ${imgs.length > 1 ? `<button class="btn gray" data-pre title="按正文重新策划内页配图并重画,会扣生图费用">重做内页配图</button>` : ""}</div>` : ""}</div>
    </div></div>`;
  preloadPv();
  if (fresh) m.querySelector(".fl-xm")?.focus({ preventScroll: true });   // 键盘 / 读屏用户打开后焦点进弹窗
}
// 只换大图和缩略图的高亮,不重绘整个弹窗
function setPvImg(k) {
  const v = FL.pv, d = v.list[v.i], n = d.images.length; if (!n) return;
  v.img = (k % n + n) % n;
  const im = $(".fl-big img"); if (!im) return drawPv();
  im.src = "/images/" + d.images[v.img]; im.alt = `第 ${v.img + 1} 张配图`;
  $$(".fl-thumbs button").forEach((b, i) => b.classList.toggle("on", i === v.img));
  preloadPv();
}
// 提前把下一张、上一篇 / 下一篇的封面拉下来,翻起来不用等
function preloadPv() {
  const v = FL.pv; if (!v) return; const d = v.list[v.i], N = v.list.length;
  const urls = [d.images[v.img + 1], v.list[(v.i + 1) % N]?.images[0], v.list[(v.i - 1 + N) % N]?.images[0]].filter(Boolean);
  urls.forEach(u => { const im = new Image(); im.src = "/images/" + u; });
}
function bindPv(m) {
  m.onclick = async e => {
    const v = FL.pv; if (!v) return;
    if (e.target === m || e.target.closest("[data-pclose]")) return closePv();
    const d = v.list[v.i];
    const s = e.target.closest("[data-pstep]"); if (s) return stepPv(+s.dataset.pstep);
    const ii = e.target.closest("[data-pimg]"); if (ii) return setPvImg(v.img + +ii.dataset.pimg);
    const to = e.target.closest("[data-pto]"); if (to) return setPvImg(+to.dataset.pto);
    if (e.target.closest("[data-pmore]")) { v.more = !v.more; return drawPv(); }
    const cp = e.target.closest("[data-pcopy]");
    if (cp) {
      const txt = cp.dataset.pcopy === "title" ? d.title : `${d.title}\n\n${d.content}\n\n${d.tags.map(t => "#" + t).join(" ")}`;
      try { await copyText(txt); toast("已复制"); } catch { toast("复制失败,请手动选中文字复制", 1); }
      return;
    }
    if (e.target.closest("[data-ppubd]")) {
      try { await api("/api/drafts/publish", "POST", { ids: [d.id], on: true, published_at: $("#pvday").value }); toast("已登记发布"); await pvRefresh(d); } catch (err) { toast(err.message, 1); }
      return;
    }
    if (e.target.closest("[data-plink]")) return linkDialog(d.id, d, () => pvRefresh(d));
    if (e.target.closest("[data-punlink]")) {
      try { await api(`/api/drafts/${d.id}/link`, "POST", { note_id: "" }); toast("已取消对应"); await pvRefresh(d); } catch (err) { toast(err.message, 1); }
      return;
    }
    if (e.target.closest("[data-ppub]")) {
      try { await api(`/api/drafts/${d.id}/published`, "POST", { on: !d.published }); d.published = !d.published; v.dirty = true; toast(d.published ? "已标记为已发布" : "已取消标记"); drawPv(); } catch (err) { toast(err.message, 1); }
      return;
    }
    if (e.target.closest("[data-pcover]")) {
      const btn = e.target.closest("[data-pcover]"), old = d.images[0];
      btn.disabled = true; btn.textContent = "换封面中…";
      try {
        await api(`/api/drafts/${d.id}/cover`, "POST"); toast("正在换封面,稍等几秒");
        for (let k = 0; k < 40 && FL.pv; k++) {
          await new Promise(r => setTimeout(r, 2500));
          const fresh = await api(`/api/library/${d.id}`).catch(() => null);
          if (fresh && fresh.images[0] && fresh.images[0] !== old && !["queued", "drawing"].includes(fresh.status)) { Object.assign(d, fresh); v.img = 0; v.dirty = true; drawPv(); return; }
        }
        toast("封面还没换好,稍后刷新看看", 1); drawPv();
      } catch (err) { toast(err.message, 1); drawPv(); }
      return;
    }
    if (e.target.closest("[data-pre]")) {
      if (!confirm("按正文重新策划内页配图并重画?封面不变,内页会重新扣生图费用。")) return;
      const btn = e.target.closest("[data-pre]"), old = d.images.slice(1).join();
      btn.disabled = true; btn.textContent = "重做中…";
      try {
        await api(`/api/drafts/${d.id}/replan_images`, "POST"); toast("正在按正文重做配图,约需几分钟");
        for (let k = 0; k < 120 && FL.pv; k++) {
          await new Promise(r => setTimeout(r, 4000));
          const fresh = await api(`/api/library/${d.id}`).catch(() => null);
          if (fresh && !["queued", "drawing"].includes(fresh.status)) { Object.assign(d, fresh); v.img = 1; v.dirty = true; drawPv(); toast(fresh.images.slice(1).join() !== old ? "内页配图已更新" : "没有更新,请看错误提示", fresh.images.slice(1).join() === old); return; }
        }
        toast("还没画完,稍后刷新看看", 1); drawPv();
      } catch (err) { toast(err.message, 1); drawPv(); }
      return;
    }
    if (e.target.closest("[data-plive]")) { if (window.lvAdd) lvAdd(d.id, d.images[v.img]); return; }
    if (e.target.closest("[data-pedit]")) { setProject(d.pid); state.advOpen = true; closePv(); go("generate"); }
  };
  m.onchange = async e => {
    const sel = e.target.closest("[data-pacc]"), v = FL.pv; if (!sel || !v) return;
    const d = v.list[v.i];
    try {
      const r = await api(`/api/drafts/${d.id}/account`, "POST", { account_id: sel.value });
      d.account_id = r.account_id; d.account_name = r.account_name; v.dirty = true; toast(r.account_name ? `已指定发布到「${r.account_name}」` : "已清除发布账号"); drawPv();
    } catch (err) { toast(err.message, 1); drawPv(); }
  };
  let x0 = null;
  m.addEventListener("touchstart", e => { x0 = e.touches[0].clientX; }, { passive: true });
  m.addEventListener("touchend", e => { if (x0 == null || !FL.pv) return; const dx = e.changedTouches[0].clientX - x0; x0 = null; if (Math.abs(dx) > 70) stepPv(dx < 0 ? 1 : -1); });
}
async function stepPv(k) {
  const v = FL.pv, N = v.list.length; v.i = (v.i + k + N) % N; v.img = 0; const d = v.list[v.i];
  if (d.content === undefined) await pvFull(d);
  if (!FL.pv || FL.pv.list[FL.pv.i] !== d) return;   // 取的过程中又翻了页 / 关了弹窗
  drawPv(); $(".fl-tb") && ($(".fl-tb").scrollTop = 0);
  [1, -1].forEach(o => pvFull(v.list[(v.i + o + N) % N]));   // 提前取相邻两篇,连着翻不用等
}
document.addEventListener("keydown", e => { if (!FL.pv) return; if (e.key === "Escape") closePv(); if (e.key === "ArrowRight") stepPv(1); if (e.key === "ArrowLeft") stepPv(-1); });


// ---------- 笔记库(只读):采集到的笔记,按赛道 / 主题 / 内容类型 / 分级找参考 ----------
const NB = { f: { track: "", topic: "", ai_type: "", kind: "", tier: "", q: "", sort: "new" }, items: [], total: 0, page: 1 };
const NB_SIZE = 36;   // 每页篇数
const NB_TIER = { viral: "爆款", hot: "热门", normal: "普通" };
const nbCard = (n, i) => `<article class="fl-nc" data-nb="${i}" tabindex="0">
  <div class="fl-cv">${n.cover ? `<img src="${esc(thumb(n.cover))}" referrerpolicy="no-referrer" loading="lazy" decoding="async" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'fl-tc',textContent:this.dataset.t}))" data-t="${esc(n.title)}">` : `<div class="fl-tc">${esc(n.title)}</div>`}
    ${n.tier && n.tier !== "normal" ? `<span class="fl-badge ${n.tier === "viral" ? "ok" : ""}">${NB_TIER[n.tier]}</span>` : ""}</div>
  <div class="fl-nt">${n.note_type === "video" ? "▶ " : ""}${esc(n.title)}</div>
  <div class="fl-ns"><span>${esc(n.topic || n.track || "")}</span><span class="sub">♥ ${fmtN(n.likes)} · ★ ${fmtN(n.collects)}</span></div>
</article>`;

async function fNoteBase() {
  const f = NB.f;
  const fa = await api(`/api/notebase/facets?track=${encodeURIComponent(f.track)}`);
  const opt = (list, cur, label, name = k => k) => `<option value="">${label}</option>` + list.map(x => `<option value="${esc(x.k)}" ${x.k === cur ? "selected" : ""}>${esc(name(x.k))}(${x.n})</option>`).join("");
  const filtered = Object.entries(f).some(([k, v]) => v !== (k === "sort" ? "new" : ""));
  $("#main").onclick = null;
  $("#main").innerHTML = `<div class="fl-bar nb-bar">
    <select data-nb-f="track" aria-label="赛道">${opt(fa.tracks, f.track, "全部赛道")}</select>
    <select data-nb-f="topic" aria-label="主题">${opt(fa.topics, f.topic, "全部主题")}</select>
    <select data-nb-f="kind" aria-label="帖子类型">${opt(fa.kinds || [], f.kind, "全部帖子类型")}</select>
    <select data-nb-f="ai_type" aria-label="内容类型">${opt(fa.types, f.ai_type, "全部类型")}</select>
    <select data-nb-f="tier" aria-label="热度分级">${opt(fa.tiers, f.tier, "全部分级", k => NB_TIER[k] || k)}</select>
    <select data-nb-f="sort" aria-label="排序"><option value="new" ${f.sort === "new" ? "selected" : ""}>最新入库</option><option value="hot" ${f.sort === "hot" ? "selected" : ""}>互动最高</option></select>
    <input id="nbq" placeholder="搜索标题 / 正文 / 作者 / 标签" value="${esc(f.q)}" style="flex:1;min-width:160px">
    ${filtered ? `<button class="btn ghost sm" id="nbreset">清除筛选</button>` : ""}</div>
    <div id="nbres"></div>`;
  // 真分页:每页只渲染 NB_SIZE 篇,换页替换内容并回到顶部;筛选 / 搜索变了回到第 1 页
  const load = async page => {
    const qs = Object.entries(f).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
    const r = await api(`/api/notebase?${qs}&offset=${(page - 1) * NB_SIZE}&limit=${NB_SIZE}`);
    const pages = Math.max(1, Math.ceil(r.total / NB_SIZE));
    if (page > pages) return load(pages);   // 筛选后总数变少,当前页已经不存在
    NB.items = r.items; NB.total = r.total; NB.page = page;
    const a = (page - 1) * NB_SIZE + 1, b = a + r.items.length - 1, pg = pager(page, r.total, NB_SIZE);
    $("#nbres").innerHTML = r.items.length
      ? `<div class="pg-top"><span class="sub">共 ${NB.total} 篇 · 第 ${a}–${b} 篇${pages > 1 ? ` · 第 ${page} / ${pages} 页` : ""}</span></div><div class="fl-grid">${r.items.map(nbCard).join("")}</div>${pg}`
      : filtered ? `<div class="empty"><b>没有符合条件的笔记</b>换个筛选或关键词试试<br><button class="btn gray" id="nbreset2">清除筛选</button></div>`
        : `<div class="empty"><b>笔记库还是空的</b>新建任务后,采集端会按关键词采集笔记,采到的会汇总在这里<br><button class="btn" data-go="new">新建任务</button></div>`;
  };
  await load(NB.page || 1);
  $$("[data-nb-f]").forEach(el => el.onchange = () => { f[el.dataset.nbF] = el.value; if (el.dataset.nbF === "track") f.topic = ""; NB.page = 1; fNoteBase(); });
  let deb; $("#nbq").oninput = e => { clearTimeout(deb); deb = setTimeout(() => { f.q = e.target.value.trim(); load(1); }, 350); };
  $("#nbreset") && ($("#nbreset").onclick = () => { NB.f = { track: "", topic: "", ai_type: "", kind: "", tier: "", q: "", sort: "new" }; NB.page = 1; fNoteBase(); });
  const open = e => {
    const g = e.target.closest("[data-pg]");
    if (g) { if (g.disabled) return; return load(+g.dataset.pg).then(() => window.scrollTo({ top: 0, behavior: "smooth" })); }
    const c = e.target.closest("[data-nb]"); if (c) nbOpen(+c.dataset.nb);
  };
  $("#nbres").onclick = e => { if (e.target.closest("#nbreset2")) return $("#nbreset").click(); open(e); };
  $("#nbres").onkeydown = e => { if (e.key === "Enter" && e.target.matches("[data-nb]")) nbOpen(+e.target.dataset.nb); };
}

function nbOpen(i) {
  // 和成品预览同一套版式:左边大图 + 缩略图,右边文字,底部操作条;手机全屏、左右滑切换
  const m = document.createElement("div"); m.className = "fl-pv";
  const opener = document.activeElement; let k = 0, x0 = null;
  const picsOf = n => n.images?.length ? n.images : (n.cover ? [n.cover] : []);
  const stat = (l, v) => `<span class="nb-st"><i>${l}</i><b>${fmtN(v)}</b></span>`;
  const draw = () => {
    const list = NB.items, n = list[i], ps = picsOf(n), N = list.length;
    m.innerHTML = `<div class="fl-sheet" role="dialog" aria-modal="true" aria-label="笔记详情">
      <div class="fl-gal"><button class="fl-xm" data-x aria-label="关闭">×</button>${ps.length ? `<div class="fl-big"><img src="${esc(ps[k])}" referrerpolicy="no-referrer" alt="第 ${k + 1} 张配图">${ps.length > 1 ? `<button class="fl-ib l" data-img="-1" aria-label="上一张">‹</button><button class="fl-ib r" data-img="1" aria-label="下一张">›</button>` : ""}</div>
        ${ps.length > 1 ? `<div class="fl-thumbs">${ps.map((u, q) => `<button class="${q === k ? "on" : ""}" data-to="${q}" aria-label="第 ${q + 1} 张"><img src="${esc(thumb(u, 240))}" referrerpolicy="no-referrer" alt=""></button>`).join("")}</div>` : ""}`
        : `<div class="fl-big"><div class="fl-tc big">${esc(n.title)}</div></div>`}</div>
      <div class="fl-txt">
        <div class="fl-th">${n.tier && n.tier !== "normal" ? `<span class="fl-badge ${n.tier === "viral" ? "ok" : ""}">${NB_TIER[n.tier]}</span>` : ""}${n.note_type === "video" ? `<span class="fl-pill">视频</span>` : ""}
          <span class="sub fl-cnt">${i + 1} / ${N}</span><button class="btn gray sm" data-step="-1" aria-label="上一篇">‹</button><button class="btn gray sm" data-step="1" aria-label="下一篇">›</button><button class="fl-x" data-x aria-label="关闭">×</button></div>
        <div class="fl-tb">
          <h2>${esc(n.title)}</h2>
          <div class="nb-stats">${stat("赞", n.likes)}${stat("藏", n.collects)}${stat("评", n.comments)}</div>
          <p class="sub">${esc(n.author || "-")}${n.published_at ? ` · 发布 ${esc(n.published_at)}` : ""}</p>
          <p class="sub">${esc(n.track || "-")} · <b>${esc(n.topic || "待识别")}</b>${n.ai_type ? ` · ${esc(n.ai_type)}` : ""}${n.kind ? ` · <b title="${esc(n.kind_why || "")}">${esc(n.kind)}${n.kind_sub ? "·" + esc(n.kind_sub) : ""}</b>` : ""}${n.kind && n.kind_rel === 0 ? ` · <span style="color:var(--err)">和产品无关</span>` : ""}${n.dup_of ? " · 重复" : ""}${n.rel === "low" ? ` · <span style="color:var(--err)">弱相关</span>` : ""}</p>
          ${n.ai_highlight ? `<div class="angle"><span>爆点</span>${esc(n.ai_highlight)}${n.ai_audience ? " · " + esc(n.ai_audience) : ""}</div>` : ""}
          <div class="fl-body">${esc(n.content) || "(没有正文)"}</div>
          ${n.tags?.length ? `<div class="fl-tags">${n.tags.map(x => "#" + esc(x)).join(" ")}</div>` : ""}</div>
        <div class="fl-tf"><button class="btn gray" data-copy>复制标题和正文</button>
          ${n.url && n.url.startsWith("http") ? `<a class="btn gray" href="${esc(n.url)}" target="_blank" rel="noreferrer">在小红书打开 ↗</a>` : ""}</div>
      </div></div>`;
  };
  const setImg = q => {   // 只换大图和缩略图高亮
    const ps = picsOf(NB.items[i]), n = ps.length; if (!n) return;
    k = (q % n + n) % n;
    const im = m.querySelector(".fl-big img"); if (!im) return draw();
    im.src = ps[k]; im.alt = `第 ${k + 1} 张配图`;
    m.querySelectorAll(".fl-thumbs button").forEach((b, t) => b.classList.toggle("on", t === k));
  };
  const close = () => { m.remove(); document.body.classList.remove("fl-lock"); document.removeEventListener("keydown", key); if (opener && opener.isConnected) opener.focus({ preventScroll: true }); };
  const step = d => { const N = NB.items.length; i = (i + d + N) % N; k = 0; draw(); m.querySelector(".fl-tb")?.scrollTo(0, 0); };
  const key = e => { if (e.key === "Escape") close(); else if (e.key === "ArrowRight") step(1); else if (e.key === "ArrowLeft") step(-1); };
  m.onclick = async e => {
    if (e.target === m || e.target.closest("[data-x]")) return close();
    const s = e.target.closest("[data-step]"); if (s) return step(+s.dataset.step);
    const im = e.target.closest("[data-img]"); if (im) return setImg(k + +im.dataset.img);
    const to = e.target.closest("[data-to]"); if (to) return setImg(+to.dataset.to);
    if (e.target.closest("[data-copy]")) { const n = NB.items[i]; try { await copyText(`${n.title}\n\n${n.content || ""}`); toast("已复制"); } catch { toast("复制失败,请手动选中文字复制", 1); } }
  };
  m.addEventListener("touchstart", e => { x0 = e.touches[0].clientX; }, { passive: true });
  m.addEventListener("touchend", e => { if (x0 == null) return; const dx = e.changedTouches[0].clientX - x0; x0 = null; if (Math.abs(dx) > 70) step(dx < 0 ? 1 : -1); });
  document.addEventListener("keydown", key); draw(); document.body.classList.add("fl-lock"); document.body.appendChild(m);
  m.querySelector(".fl-xm")?.focus({ preventScroll: true });
}

// ---------- 复盘:发布 → 数据回流 → 谁爆了 → 继续复刻 ----------
const LP = { tier: "", reg: "todo", sel: new Set(), page: 1, regPage: 1, winAll: false };
const LP_WIN = 6;   // 爆款榜默认先列几篇
const LP_SIZE = 20;   // 复盘页每个列表每页条数
const TIER_CLS = { winner: "bad", good: "ok", normal: "", weak: "", observing: "warn", nodata: "", unpublished: "" };
const lpPct = r => r == null ? "-" : Math.round(r * 100) + "%";
const lpEff = (name, e) => `<div class="lp-eff"><span class="sub">${name}</span><b>${lpPct(e.rate)}</b><span class="sub">${e.winners} 爆 / ${e.mature} 篇有结论${e.lift_avg ? ` · 平均 ×${e.lift_avg}` : ""}</span></div>`;
function lpWinner(r) {
  return `<article class="lp-w" data-open="${r.id}" tabindex="0">
    <div class="lp-wt"><span class="fl-pill bad">爆款 ×${r.lift ?? "-"}</span>${r.generation ? `<span class="fl-pill">第 ${r.generation} 代</span>` : ""}${r.spawned ? `<span class="fl-pill ok">已复刻 ${r.spawned} 轮</span>` : ""}</div>
    <b class="lp-title">${esc(r.title)}</b>
    <div class="sub">${esc(r.account_name || "未指定账号")} · ${esc(r.published_at || "")}${r.days != null ? ` · 发布 ${r.days} 天` : ""}</div>
    <div class="lp-m"><span>阅读 <b>${fmtN(r.views)}</b></span><span>赞 <b>${fmtN(r.likes)}</b></span><span>藏 <b>${fmtN(r.collects)}</b></span><span>评 <b>${fmtN(r.comments)}</b></span></div>
    <div class="lp-act"><select data-spn aria-label="复刻篇数">${[3, 5, 10, 20].map(n => `<option value="${n}">${n} 篇</option>`).join("")}</select><button class="btn sm" data-spawn="${r.id}">继续复刻</button></div>
  </article>`;
}
// 手动把稿子对上千帆里的一篇笔记:列出还没对上任何稿子的千帆笔记,同账号、标题更像的排前面,可搜索
function linkDialog(did, draft, onDone) {
  const m = document.createElement("div"); m.className = "mask"; m.style.zIndex = 60;   // 要盖在成品预览(z-index 40)上面
  m.innerHTML = `<div class="card lp-dlg"><div class="card-h"><b>对上千帆笔记</b><button class="btn ghost sm" data-x>关闭</button></div>
    <p class="sub" style="margin:0 0 8px">稿子:${esc(draft ? draft.title : "")}</p><input id="lq" placeholder="搜索千帆里的笔记标题" style="width:100%;margin-bottom:8px"><div class="lp-cl" id="lcl"><div class="sub" style="padding:12px">加载中…</div></div></div>`;
  document.body.appendChild(m);
  const close = () => m.remove();
  const load = async () => {
    const r = await api(`/api/loop/notes?draft_id=${did}&q=${encodeURIComponent($("#lq").value.trim())}`).catch(() => ({ items: [] }));
    $("#lcl").innerHTML = r.items.length ? r.items.map(n => `<button class="lp-c" data-nid="${esc(n.note_id)}"><b>${esc(n.title)}</b><span class="sub">${esc(n.account_name || "")}${n.published_at ? " · " + esc(n.published_at) : ""} · 阅读 ${fmtN(n.views)} · 赞藏评 ${fmtN((n.likes || 0) + (n.collects || 0) + (n.comments || 0))}${n.sim >= 0.4 ? " · 标题相近" : ""}${n.same_account ? " · 同账号" : ""}</span></button>`).join("")
      : `<div class="empty" style="border:0">没有可对的笔记。可能千帆数据还没同步,或都已经对上了稿子。</div>`;
  };
  let t; $("#lq").oninput = () => { clearTimeout(t); t = setTimeout(load, 300); };
  m.onclick = async e => {
    if (e.target === m || e.target.closest("[data-x]")) return close();
    const c = e.target.closest("[data-nid]"); if (!c) return;
    try { await api(`/api/drafts/${did}/link`, "POST", { note_id: c.dataset.nid }); toast("已对上,数据会出现在复盘里"); close(); onDone ? onDone() : fLoop(); } catch (err) { toast(err.message, 1); }
  };
  load();
}
// 「数据复盘」一个入口,里面切换:复盘(稿子表现、爆款)和数据看板(千帆账号数据),切换条放在页头右侧
const dataSwitch = cur => `<div class="seg" role="tablist" aria-label="数据复盘">${[["loop", "复盘"], ["data", "数据看板"]].map(([k, n]) => `<button role="tab" aria-selected="${k === cur}" class="${k === cur ? "on" : ""}" data-dk="${k}">${n}</button>`).join("")}</div>`;
document.addEventListener("click", e => { const b = e.target.closest("[data-dk]"); if (b && !b.classList.contains("on")) go(b.dataset.dk); });
async function fLoop() {
  $("#pa").innerHTML = dataSwitch("loop");
  const pg = await api(`/api/loop/page?tier=${LP.tier}&page=${LP.page}&reg=${LP.reg}&regpage=${LP.regPage}&size=${LP_SIZE}`);   // 一次请求,各列表按页取
  LP.page = pg.rows.page; LP.regPage = pg.reg.page;   // 数据变少时服务端会把页码拉回有效范围
  const ov = pg.overview, dr = { data_at: pg.data_at }, c = pg.cfg, rows = pg.rows.items, wins = pg.winners, regList = pg.reg.items;
  const nTodo = ov.tiers.unpublished ?? 0, nNodata = ov.tiers.nodata ?? 0, nWin = ov.tiers.winner ?? 0;
  const today = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  // 发布登记:紧凑的笔记卡片(左边封面、右边标题和账号),一屏能放下一批;待登记的整张卡片可点选
  const regCv = r => `<span class="lp-cv">${r.cover ? `<img src="${esc(thumb("/images/" + r.cover, 240))}" alt="" loading="lazy" decoding="async">` : `<i>${esc((r.title || "").slice(0, 4))}</i>`}</span>`;
  const regRow = r => LP.reg === "todo"
    ? `<label class="lp-rc ${LP.sel.has(r.id) ? "on" : ""}" data-id="${r.id}"><input type="checkbox" data-rsel="${r.id}" ${LP.sel.has(r.id) ? "checked" : ""} aria-label="选择这篇">${regCv(r)}<span class="lp-ci"><b>${esc(r.title)}</b><span class="sub">${esc(r.account_name || "未指定账号")}</span></span></label>`
    : `<div class="lp-rc nd">${regCv(r)}<span class="lp-ci"><b>${esc(r.title)}</b><span class="sub">${esc(r.account_name || "未指定账号")} · 标了已发布${r.published_at ? `(${esc(String(r.published_at).slice(0, 10))})` : ""}</span><span class="lp-ca"><button class="btn gray sm" data-link="${r.id}">对上千帆笔记</button><button class="btn ghost sm" data-unpub="${r.id}" title="其实还没发,取消已发布标记">取消标记</button></span></span></div>`;
  const regBlock = `<section class="card" style="margin-top:12px"><div class="card-h"><b>发布登记</b><span class="sub">发布后系统会按标题自动对上千帆数据;这里处理还没登记的和对不上的</span></div>
    <div class="fl-chips" style="margin-bottom:10px"><button class="chip ${LP.reg === "todo" ? "on" : ""}" data-rtab="todo">待登记发布 ${nTodo}</button><button class="chip ${LP.reg === "nodata" ? "on" : ""}" data-rtab="nodata">标了发布但没对上数据 ${nNodata}</button></div>
    ${regList.length ? `${LP.reg === "todo" ? `<div class="lp-rbar"><button class="btn gray sm" data-rall>${regList.length && regList.every(r => LP.sel.has(r.id)) ? "取消全选" : `全选本页 ${regList.length} 篇`}</button><span class="sub">已选 <b>${LP.sel.size}</b> 篇</span><label class="sub" style="margin-left:auto">发布日期 <input type="date" id="rday" value="${today}" max="${today}" style="width:auto"></label><button class="btn sm" data-rpub ${LP.sel.size ? "" : "disabled"}>标记已发布</button></div>` : `<p class="sub" style="margin:0 0 8px">一般是发布时改过标题,或千帆数据还没同步。点「对上千帆笔记」从千帆里挑一篇手动对上;确实还没发的可以取消标记。</p>`}
    <div class="lp-rcs">${regList.map(regRow).join("")}</div>${pager(LP.regPage, pg.reg.total, LP_SIZE, "rpg")}`
      : `<div class="empty" style="border:0;padding:18px">${LP.reg === "todo" ? "所有稿子都登记过了" : "没有对不上的稿子"}</div>`}</section>`;
  const fun = ov.funnel, mx = Math.max(1, ...fun.map(f => f.n));
  const se = ov.spawn_effect;
  const names = { "": "全部", winner: "爆款", good: "不错", normal: "一般", weak: "偏弱", observing: "观察中", nodata: "没对上数据", unpublished: "未发布" };
  const tbl = (head, body) => `<table class="tbl"><thead><tr><th>${head}</th><th class="num">有结论</th><th class="num">爆款</th><th class="num">爆款率</th></tr></thead><tbody>${body}</tbody></table>`;
  const topicRows = ov.by_topic.map(x => `<tr><td>${esc(x.name)}</td><td class="num">${x.mature}</td><td class="num">${x.winners}</td><td class="num">${lpPct(x.rate)}</td></tr>`).join("");
  const projRows = ov.by_project.map(x => `<tr><td>${esc(x.name)}<div class="sub">${esc(x.created)}</div></td><td class="num">${x.mature}</td><td class="num">${x.winners}</td><td class="num">${lpPct(x.rate)}</td></tr>`).join("");
  const ptypeRows = (ov.by_ptype || []).map(x => `<tr><td>${esc(x.name)}</td><td class="num">${x.mature}</td><td class="num">${x.winners}</td><td class="num">${lpPct(x.rate)}</td></tr>`).join("");
  const coverRows = (ov.by_cover || []).map(x => `<tr><td>${esc(x.name)}</td><td class="num">${x.mature}</td><td class="num">${x.winners}</td><td class="num">${lpPct(x.rate)}</td></tr>`).join("");
  const none = `<div class="empty" style="border:0">数据还不够</div>`;
  $("#main").innerHTML = `${ov.no_data ? `<div class="banner warn">还没有千帆里的「笔记表现」数据。采集端把这份数据同步过来后,系统会自动把发出去的稿子对上号、判断谁爆了。</div>` : ""}
  <section class="card lp-fun"><div class="card-h"><b>从生成到爆款</b><span class="sub">稿子发布后,系统按标题自动和千帆里的笔记对上号,不用手动登记</span></div>
    <div class="lp-steps">${fun.map((f, i) => `<div class="lp-step ${f.key === "winner" ? "win" : ""}"><b>${f.n}</b><span>${f.name}</span><i style="width:${Math.round(f.n / mx * 100)}%"></i></div>${i < fun.length - 1 ? `<s aria-hidden="true">›</s>` : ""}`).join("")}</div></section>
  ${regBlock}
  <section class="card" style="margin-top:12px"><div class="card-h"><b>继续复刻有没有用</b><span class="sub">对比「普通复刻」和「以爆款为样本继续复刻」两类稿子的爆款率</span></div>
    <div class="lp-effs">${lpEff("普通复刻稿", se.original)}${lpEff("爆款样本复刻稿", se.spawned)}</div>
    ${se.spawned.mature < 5 ? `<p class="sub" style="margin:8px 0 0">爆款样本复刻稿的有结论数据还很少(${se.spawned.mature} 篇),数据多了再看这个对比更可靠。</p>` : ""}</section>
  <h3 class="lp-h">爆款榜 <span class="sub">${nWin} 篇${nWin > wins.length ? `(只列前 ${wins.length} 篇)` : ""} · 发布满 ${c.min_days} 天、互动 ≥ ${c.min_eng} 且 ≥ 本账号平均的 ${c.win_lift} 倍</span></h3>
  ${wins.length ? `<div class="lp-ws">${(LP.winAll ? wins : wins.slice(0, LP_WIN)).map(lpWinner).join("")}</div>${wins.length > LP_WIN ? `<p style="text-align:center;margin:12px 0 0"><button class="btn gray sm" data-winall>${LP.winAll ? "收起" : `展开全部 ${wins.length} 篇`}</button></p>` : ""}` : `<div class="empty"><b>还没有判定为爆款的稿子</b>先多发布一些稿子;发布满 ${c.min_days} 天、数据回流后会出现在这里。门槛可以在管理端「接口配置 → 爆款回流」调整。</div>`}
  <h3 class="lp-h">哪类更容易爆</h3>
  <div class="lp-two"><section class="card flush"><div class="card-h" style="padding:12px 14px"><b>按选题主题</b></div>${ov.by_topic.length ? tbl("主题", topicRows) : none}</section>
  <section class="card flush"><div class="card-h" style="padding:12px 14px"><b>按任务批次</b></div>${ov.by_project.length ? tbl("任务", projRows) : none}</section>
  <section class="card flush"><div class="card-h" style="padding:12px 14px"><b>按封面类型</b></div>${(ov.by_cover || []).length ? tbl("封面", coverRows) : none}</section>
  <section class="card flush"><div class="card-h" style="padding:12px 14px"><b>按帖子类型</b></div>${(ov.by_ptype || []).length ? tbl("类型", ptypeRows) : none}</section></div>
  <h3 class="lp-h">全部稿子的表现</h3>
  <div class="fl-chips" style="margin-bottom:10px">${Object.entries(names).map(([k, n]) => `<button class="chip ${LP.tier === k ? "on" : ""}" data-lt="${k}">${n}${k ? ` ${ov.tiers[k] ?? 0}` : ""}</button>`).join("")}</div>
  <section class="card flush">${rows.length ? `<table class="tbl lp-tbl"><thead><tr><th>稿子</th><th>账号</th><th>发布</th><th class="num">阅读</th><th class="num">赞藏评</th><th class="num">相对平均</th><th>判定</th></tr></thead><tbody>${rows.map(r => `<tr data-open="${r.id}"><td class="lp-tt">${esc(r.title)}${r.generation ? ` <span class="sub">· 第${r.generation}代</span>` : ""}</td><td class="sub">${esc(r.account_name || "-")}</td><td class="sub">${esc(r.published_at || "-")}</td><td class="num">${r.views == null ? "-" : fmtN(r.views)}</td><td class="num">${r.eng == null ? "-" : r.eng}</td><td class="num">${r.lift == null ? "-" : "×" + r.lift}</td><td><span class="fl-pill ${TIER_CLS[r.tier]}">${r.tier_name}</span></td></tr>`).join("")}</tbody></table>` : `<div class="empty" style="border:0">这个筛选下没有稿子</div>`}</section>
  ${pager(LP.page, pg.rows.total, LP_SIZE, "lpg")}
  <p class="sub" style="margin:14px 2px 0">「相对平均」= 这篇的互动(赞+藏+评)÷ 该账号所有笔记的平均(去掉最高 5%);账号互动很少时改比阅读量。刚发布的稿子数据还没沉淀,标为「观察中」,不下结论。阈值由管理端调整。数据更新于 ${esc(String(dr.data_at || "-").slice(0, 16))}。</p>`;
  // 勾选 / 全选只改界面,不用重新请求整页
  const syncSel = () => {
    const n = LP.sel.size, all = regList.length && regList.every(r => LP.sel.has(r.id));
    const cnt = $(".lp-rbar b"); if (cnt) cnt.textContent = n;
    const pub = $("[data-rpub]"); if (pub) pub.disabled = !n;
    const ba = $("[data-rall]"); if (ba) ba.textContent = all ? "取消全选" : `全选本页 ${regList.length} 篇`;
    $$(".lp-rc[data-id]").forEach(l => { const on = LP.sel.has(+l.dataset.id); l.classList.toggle("on", on); l.querySelector("input").checked = on; });
  };
  let lib = null;
  $("#main").onclick = async e => {
    const lt = e.target.closest("[data-lt]"); if (lt) { LP.tier = lt.dataset.lt; LP.page = 1; return fLoop(); }
    if (e.target.closest("[data-winall]")) { LP.winAll = !LP.winAll; return fLoop(); }
    const gp = e.target.closest("[data-lpg]"); if (gp) { if (gp.disabled) return; LP.page = +gp.dataset.lpg; return fLoop(); }
    const gr = e.target.closest("[data-rpg]"); if (gr) { if (gr.disabled) return; LP.regPage = +gr.dataset.rpg; return fLoop(); }
    const rt = e.target.closest("[data-rtab]"); if (rt) { LP.reg = rt.dataset.rtab; LP.regPage = 1; LP.sel.clear(); return fLoop(); }
    const rs = e.target.closest("[data-rsel]"); if (rs) { const id = +rs.dataset.rsel; LP.sel.has(id) ? LP.sel.delete(id) : LP.sel.add(id); return syncSel(); }
    if (e.target.closest("[data-rall]")) { LP.sel = regList.length && regList.every(r => LP.sel.has(r.id)) ? new Set() : new Set([...LP.sel, ...regList.map(r => r.id)]); return syncSel(); }
    if (e.target.closest("[data-rpub]")) {
      try { const r = await api("/api/drafts/publish", "POST", { ids: [...LP.sel], on: true, published_at: $("#rday").value }); toast(`已登记 ${r.n} 篇为已发布`); LP.sel.clear(); return fLoop(); } catch (err) { return toast(err.message, 1); }
    }
    const up = e.target.closest("[data-unpub]");
    if (up) { try { await api("/api/drafts/publish", "POST", { ids: [+up.dataset.unpub], on: false }); toast("已取消发布标记"); return fLoop(); } catch (err) { return toast(err.message, 1); } }
    const lk = e.target.closest("[data-link]"); if (lk) return linkDialog(+lk.dataset.link, regList.find(r => r.id === +lk.dataset.link));
    const sp = e.target.closest("[data-spawn]");
    if (sp) {
      const id = +sp.dataset.spawn, n = +sp.closest(".lp-act").querySelector("[data-spn]").value;
      const w = wins.find(x => x.id === id);
      if (!confirm(`以《${w ? w.title : "这篇稿子"}》为样本继续复刻 ${n} 篇?\n会新建一个任务,消耗文案和生图额度。`)) return;
      sp.disabled = true;
      try { await api(`/api/drafts/${id}/spawn`, "POST", { count: n }); toast("已开始继续复刻,可在「任务」里看进度"); return go("runs"); }
      catch (err) { toast(err.message, 1); sp.disabled = false; }
      return;
    }
    if (e.target.closest("select")) return;
    const o = e.target.closest("[data-open]"); if (!o) return;
    lib = lib || await api("/api/library?brief=1"); fOpen(+o.dataset.open, lib);
  };
}

window.FLOW_VIEWS = {
  new: { t: "新建任务", d: "选赛道或上传产品,填好篇数,剩下的交给流水线", fn: fNew },
  runs: { t: "任务", d: "每个任务从采集到出成品全自动,出了问题会在这里标出来", fn: fRuns },
  run: { t: "任务详情", d: "", fn: fRun, nav: "runs" },
  lib: { t: "成品库", d: "点开一篇就能复制、存图、标记已发布", fn: fLib },
  loop: { t: "复盘", d: "发出去的稿子谁爆了、哪类更容易爆,一键以爆款为样本继续复刻", fn: fLoop },
  notebase: { t: "笔记库", d: "采集到的笔记,按赛道、主题、热度找参考,点开看全文和配图", fn: fNoteBase },
  data: { t: "数据看板", d: "各账号的粉丝、笔记表现,看哪类选题更好、最近什么在火", nav: "loop", fn: () => { window.QF_STAFF = true; return vQianfan(); } },
};
