// ---------- 达人库:千帆买手广场采集到的合作达人,按分类/量级/适配度筛选,记录建联进度 ----------
const KL = { q: "", category: "", tier: "", status: "", fit: 0, sort: "score", mine: 0, offset: 0, limit: 30 };
const klFit = r => r.fit === 2 ? `<span class="fl-pill ok" title="历史合作过我们这类品类,或愿意合作这类品类">带过同类品</span>` : r.fit === 1 ? `<span class="fl-pill warn" title="内容分类和我们产品相关,但没带过同类货">内容相关</span>` : `<span class="sub">-</span>`;
const klHit = (x, hot) => hot.some(t => x.includes(t) || t.includes(x));
const klChips = (xs, hot = []) => xs.length ? `<div class="kl-chips">${xs.slice(0, 4).map(x => `<span class="kl-c ${klHit(x, hot) ? "hit" : ""}" title="${esc(x)}">${esc(x.split("/")[0])}</span>`).join("")}${xs.length > 4 ? `<span class="sub">+${xs.length - 4}</span>` : ""}</div>` : `<span class="sub">-</span>`;
const klQuery = () => new URLSearchParams(Object.entries({ q: KL.q, category: KL.category, tier: KL.tier, status: KL.status, fit: KL.fit || "", sort: KL.sort, mine: KL.mine || "" }).filter(([, v]) => v !== "" && v != null)).toString();
// 认领跟进:没人跟进 → 认领;自己在跟进 → 放手;别人在跟进 → 只显示是谁
const klClaim = (x, me) => !x.owner ? `<button class="btn gray sm" data-claim="1" title="由我来跟进这位达人">认领跟进</button>`
  : x.owner === me ? `<div class="kl-mine"><span class="fl-pill ok">我在跟进</span><button class="btn ghost sm" data-claim="0" title="不再跟进,让别人可以认领">放手</button></div>`
  : `<div class="sub" style="text-align:center">${esc(x.owner)} 跟进中</div>`;
const klPct = v => v ? Math.round(v * 100) + "%" : "-";

// 联系方式:列表里只显示打码的手机号;点「查看」才取明文。获取要管理员开启、每天有上限
const klContact = (x, r) => x.has_contact ? `<button class="btn sm kl-ok" data-ct="view" title="${esc(x.phone_masked || "")}">查看联系方式</button>`
  : x.contact_status === "queued" ? `<button class="btn gray sm" disabled>获取中…</button>`
  : x.contact_status === "none" ? `<button class="btn gray sm" disabled title="对方没有开放联系方式">对方未开放</button>`
  : r.contact_enabled ? `<button class="btn gray sm" data-ct="get" title="${x.contact_status === "error" ? "上次失败:" + esc(x.contact_error || "") : "让采集端去千帆读取这位达人的联系方式(今天还能取 " + r.contact_left + " 位)"}">${x.contact_status === "error" ? "重试获取" : "获取联系方式"}</button>`
  : `<button class="btn gray sm" disabled title="管理员在「接口配置 → 达人采集」里开启后才能获取">获取联系方式</button>`;

// 列表区(统计句 + 表格 + 分页):搜索时只刷新这一块,不动上面的搜索框和筛选条
function klRes(r) {
  const f = r.facets, tg = r.targets || [], ctg = r.content_targets || [];
  const chipLine = (label, xs, hot) => xs.length ? `<div class="kl-line"><em>${label}</em>${klChips(xs, hot)}</div>` : "";
  const rows = r.items.map(x => `<tr data-id="${x.id}">
    <td><div class="kl-who"><span class="kl-av">${x.avatar ? `<img src="${esc(x.avatar)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}</span>
      <div class="kl-nm"><div class="kl-n1"><b>${esc(x.name || "(未命名)")}</b>${x.level ? `<span class="kl-lv">${esc(x.level)}</span>` : ""}</div><div class="sub">${esc([x.city, x.sex].filter(Boolean).join(" · "))}</div>${x.live_plan ? `<div class="sub kl-lp" title="${esc(x.live_plan)}">直播 ${esc(x.live_plan)}</div>` : ""}</div></div></td>
    <td class="num"><b>${fmtN(x.fans)}</b><div class="sub">${esc(x.tier)}</div></td>
    <td class="kl-cats">${chipLine("内容", x.categories, ctg)}${chipLine("带货", x.history_cats.concat(x.willing || []), tg)}${!x.categories.length && !x.history_cats.length ? `<span class="sub">-</span>` : ""}</td>
    <td class="num"><b>${esc(x.gmv_text || "-")}</b><div class="sub">客单价 ${esc(x.price_text || "-")}</div>${x.brand_gmv ? `<div class="sub">品牌场均 ${esc(x.brand_gmv)}</div>` : ""}</td>
    <td class="num">${x.avg_viewers ? `场均观看 ${fmtN(x.avg_viewers)}` : "-"}<div class="sub">${x.live_days ? `直播 ${x.live_days} 天` : ""}${x.note_read_median ? ` · 笔记阅读 ${fmtN(x.note_read_median)}` : ""}</div><div class="sub">活跃粉丝 ${klPct(x.active_rate)}</div></td>
    <td class="kl-fitc"><div class="kl-fitcol">${klFit(x)}<span class="fl-pill kl-score" title="综合评分(0-100):适配度、合作意愿、量级、销售额、活跃粉丝率">评分 ${x.score}</span>${x.coop_tags.map(t => `<span class="kl-c ${/积极|回复率高/.test(t) ? "hit" : ""}">${esc(t)}</span>`).join("")}</div></td>
    <td class="kl-actc"><div class="kl-act"><select class="kl-st st-${x.status}" data-st aria-label="建联状态">${f.statuses.map(s => `<option value="${s.key}" ${x.status === s.key ? "selected" : ""}>${s.name}</option>`).join("")}</select>${klContact(x, r)}<button class="btn gray sm" data-note>${x.note ? "编辑备注" : "写备注"}</button>${x.note ? `<div class="kl-notep" data-note title="${esc(x.note)}">${esc(x.note)}</div>` : ""}${klClaim(x, r.me)}${x.url ? `<a class="btn gray sm kl-home" href="${esc(x.url)}" target="_blank" rel="noopener noreferrer" title="在小红书打开这位达人的主页"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>小红书主页</a>` : ""}</div></td></tr>`).join("");
  const pages = Math.ceil(r.total / KL.limit), cur = Math.floor(KL.offset / KL.limit) + 1;
  return `
  <p class="sub" style="margin:4px 2px 10px">共 ${r.total} 位${r.last_sync ? ` · 最近采集 ${esc(String(r.last_sync).slice(0, 16))}` : ""}${tg.length ? ` · 我们的目标品类:${tg.map(esc).join("、")}(命中的标橙)` : ""}。「历史合作」是达人以前带过哪些品类的货;评分是参考排序,不代表对方意愿;联系方式请在千帆里「发起邀约」时获取。</p>
  <section class="card flush kl-wrap">${rows ? `<table class="tbl kl-tbl"><thead><tr><th>达人</th><th class="num">粉丝</th><th>分类</th><th class="num">场均销售额</th><th class="num">直播 / 笔记</th><th>适配 / 合作意愿</th><th class="kl-actc">建联 / 操作</th></tr></thead><tbody>${rows}</tbody></table>` : `<div class="empty" style="border:0">没有符合筛选的达人</div>`}</section>
  ${pages > 1 ? `<div class="kl-pg"><button class="btn gray sm" data-pg="-1" ${cur <= 1 ? "disabled" : ""}>上一页</button><span class="sub">${cur} / ${pages}</span><button class="btn gray sm" data-pg="1" ${cur >= pages ? "disabled" : ""}>下一页</button></div>` : ""}`;
}

const klSchedulePoll = () => {   // 有在获取联系方式的:隔 15 秒刷新一次;用户正在搜索框里打字时先等等
  clearTimeout(KL.poll);
  KL.poll = setTimeout(() => {
    if (state.tab !== "kol") return;
    if (KL.composing || (document.activeElement && document.activeElement.id === "klq")) return klSchedulePoll();
    fKol();
  }, 15000);
};

async function klSearch() {   // 搜索只换列表区,搜索框本身不动(重新渲染会让输入法候选框和光标丢掉)
  const seq = KL.seq = (KL.seq || 0) + 1;
  const r = await api(`/api/kol?${klQuery()}&limit=${KL.limit}&offset=${KL.offset}`);
  if (seq !== KL.seq) return;
  const res = $("#klres"); if (!res) return fKol();
  KL.last = r; res.innerHTML = klRes(r);
  $$("#pa a[href^='/api/kol/export.csv']").forEach(a => { a.href = `/api/kol/export.csv?${klQuery()}${a.href.includes("contact=1") ? "&contact=1" : ""}`; });
}

async function fKol() {
  const seq = KL.seq = (KL.seq || 0) + 1;   // 连续触发时只认最后一次请求的结果,旧的慢请求回来也不覆盖
  const r = await api(`/api/kol?${klQuery()}&limit=${KL.limit}&offset=${KL.offset}`);
  if (seq !== KL.seq) return;
  KL.last = r;
  const f = r.facets, tg = r.targets || [], ctg = r.content_targets || [];
  $("#pa").innerHTML = r.all ? `${r.contact_enabled ? `<button class="btn gray" id="klcb" title="给「已入围」状态、还没有联系方式的达人,让采集端逐位去千帆读取联系方式">获取已入围达人的联系方式</button><a class="btn gray" href="/api/kol/export.csv?${klQuery()}&contact=1" download title="含手机号、微信号,请妥善保管">导出(含联系方式)</a>` : ""}<a class="btn gray" href="/api/kol/export.csv?${klQuery()}" download>导出 CSV</a>` : "";
  const opt = (v, n, cur) => `<option value="${esc(v)}" ${String(cur) === String(v) ? "selected" : ""}>${esc(n)}</option>`;
  const catSel = `<select id="klcat" aria-label="分类"><option value="">全部分类</option>
    <optgroup label="内容分类">${f.categories.map(c => opt(c.name, `${c.name}(${c.n})`, KL.category)).join("")}</optgroup>
    <optgroup label="历史合作品类">${f.history.map(c => opt(c.name, `${c.name}(${c.n})`, KL.category)).join("")}</optgroup></select>`;
  // 搜索框:重新渲染时沿用用户正在输入的那个输入框(否则输入到一半被换掉,焦点丢了,输入法也被打断)
  const keepQ = $("#klq"), qFocus = !!keepQ && document.activeElement === keepQ, qSel = keepQ ? [keepQ.selectionStart, keepQ.selectionEnd] : null;
  $("#main").innerHTML = `${!r.all ? `<div class="empty"><b>达人库还是空的</b>${r.syncing ? "采集端正在去千帆「买手广场」采集,稍等几分钟再刷新。" : r.enabled ? "已开启自动采集,采集端领到任务后会把买手广场的达人同步过来。" : "让采集端去千帆「买手广场」采集:管理员在管理端「接口配置 → 达人采集」里点「立即采集」,或打开自动采集。"}</div>` : `
  <div class="kl-top"><div class="kl-stat"><b>${r.all}</b><span>位达人</span></div><div class="kl-stat"><b>${r.new_7d}</b><span>近 7 天新增</span></div>
    <div class="kl-seg" role="tablist" aria-label="按建联状态筛选"><button class="${KL.mine ? "on" : ""}" data-mine role="tab" title="只看我认领跟进的达人">我跟进的 <i>${r.mine_n || 0}</i></button><button class="${KL.status === "" && !KL.mine ? "on" : ""}" data-fs="" role="tab">全部 <i>${r.all}</i></button>${f.statuses.map(s => `<button class="${KL.status === s.key ? "on" : ""} st-${s.key}" data-fs="${s.key}" role="tab"><b class="dot"></b>${s.name} <i>${s.n}</i></button>`).join("")}</div></div>
  <div class="kl-bar"><input id="klq" placeholder="搜索昵称、城市、品类" value="${esc(KL.q)}">${catSel}
    <select id="kltier" aria-label="量级"><option value="">全部量级</option>${f.tiers.map(t => opt(t.name, `${t.name}(${t.n})`, KL.tier)).join("")}</select>
    <select id="klfit" aria-label="适配度"><option value="">适配度不限</option>${opt(2, "只看带过同类品的", KL.fit)}${opt(1, "带过同类品 + 内容相关", KL.fit)}</select>
    <select id="klsort" aria-label="排序">${[["score", "综合评分"], ["fans", "粉丝数"], ["gmv", "场均销售额"], ["new", "最近发现"]].map(([v, n]) => opt(v, `按${n}排序`, KL.sort)).join("")}</select></div>
  <div id="klres">${klRes(r)}</div>`}`;
  if (keepQ && $("#klq")) { $("#klq").replaceWith(keepQ); if (qFocus) { keepQ.focus(); try { keepQ.setSelectionRange(qSel[0], qSel[1]); } catch { } } }
  if (!r.all) return;
  if (r.items.some(x => x.contact_status === "queued")) klSchedulePoll();   // 有在获取的:隔 15 秒刷新一次
  if (r.contact_paused) $("#main").insertAdjacentHTML("afterbegin", `<div class="banner err">获取联系方式已自动暂停:${esc(r.contact_paused)}。请先在千帆里手动确认账号是否正常,管理员再到「接口配置 → 达人采集」重新打开。</div>`);
  const cb = $("#klcb");
  if (cb) cb.onclick = async () => {
    if (!confirm(`给「已入围」状态的达人批量获取联系方式?\n今天还能取 ${r.contact_left} 位;每位要让采集端在千帆里打开一次邀约弹窗,大约 10 秒一位。`)) return;
    cb.disabled = true;
    try { const o = await api("/api/kol/contact/batch", "POST", { status: "shortlist" }); toast(o.queued ? `已下发 ${o.queued} 位${o.stopped ? `(${o.stopped})` : ""}` : (o.stopped || "没有需要获取的达人(先把达人标成「已入围」)"), o.queued ? 0 : 1); fKol(); } catch (err) { toast(err.message, 1); cb.disabled = false; }
  };
  const again = () => { KL.offset = 0; fKol(); };
  // 搜索:输入法拼音还没选字时(isComposing)不搜,选完字再搜;停手 400 毫秒自动搜,按回车立刻搜;内容没变就不重复请求
  const qbox = $("#klq");
  const runSearch = () => { clearTimeout(KL.qt); if (KL.composing) return; const v = qbox.value.trim(); if (v !== KL.q) { KL.q = v; KL.offset = 0; klSearch(); } };
  qbox.oninput = e => { if (e.isComposing) return; clearTimeout(KL.qt); KL.qt = setTimeout(runSearch, 400); };
  KL.runSearch = runSearch;   // 输入框会被沿用,compositionend 没有 on* 属性,只能 addEventListener,且只绑一次
  if (!qbox._klBound) {
    qbox._klBound = true;
    qbox.addEventListener("compositionstart", () => { KL.composing = true; clearTimeout(KL.qt); });   // 拼音输入中:取消待发的搜索
    qbox.addEventListener("compositionend", () => { KL.composing = false; clearTimeout(KL.qt); KL.qt = setTimeout(() => KL.runSearch(), 400); });
  }
  qbox.onkeydown = e => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); runSearch(); } };
  $("#klcat").onchange = e => { KL.category = e.target.value; again(); };
  $("#kltier").onchange = e => { KL.tier = e.target.value; again(); };
  $("#klfit").onchange = e => { KL.fit = +e.target.value || 0; again(); };
  $("#klsort").onchange = e => { KL.sort = e.target.value; again(); };
  $("#main").onclick = e => {
    const r = KL.last, f = r.facets;
    if (e.target.closest("[data-mine]")) { KL.mine = KL.mine ? 0 : 1; return again(); }
    const fs = e.target.closest("[data-fs]"); if (fs) { KL.mine = 0; KL.status = KL.status === fs.dataset.fs ? "" : fs.dataset.fs; return again(); }
    const cl = e.target.closest("[data-claim]");
    if (cl) { cl.disabled = true; return api(`/api/kol/${cl.closest("tr").dataset.id}/claim`, "POST", { on: cl.dataset.claim === "1" }).then(() => { toast(cl.dataset.claim === "1" ? "已认领,由你跟进" : "已放手"); fKol(); }).catch(err => { toast(err.message, 1); cl.disabled = false; }); }
    const pg = e.target.closest("[data-pg]"); if (pg) { KL.offset = Math.max(0, KL.offset + KL.limit * +pg.dataset.pg); return fKol(); }
    const ct = e.target.closest("[data-ct]");
    if (ct) {
      const x = r.items.find(i => i.id === +ct.closest("tr").dataset.id);
      if (ct.dataset.ct === "get") return api(`/api/kol/${x.id}/contact`, "POST").then(o => { toast(o.queued ? "已下发,采集端读取中,几十秒后刷新" : o.reason || "已处理"); fKol(); }).catch(err => toast(err.message, 1));
      return klContactView(x);
    }
    const nb = e.target.closest("[data-note]"); if (nb) return klNote(r.items.find(x => x.id === +nb.closest("tr").dataset.id), f.statuses);
  };
  $("#main").onchange = async e => {
    const r = KL.last, f = r.facets;
    const st = e.target.closest("[data-st]"); if (!st) return;
    try { await api(`/api/kol/${st.closest("tr").dataset.id}`, "POST", { status: st.value }); toast("已更新状态"); fKol(); } catch (err) { toast(err.message, 1); }
  };
}

async function klContactView(x) {
  let c; try { c = await api(`/api/kol/${x.id}/contact`); } catch (err) { return toast(err.message, 1); }
  const m = document.createElement("div"); m.className = "mask";
  const row = (label, v) => v ? `<div class="kl-cr"><span class="sub">${label}</span><b>${esc(v)}</b><button class="btn gray sm" data-cp="${esc(v)}">复制</button></div>` : "";
  m.innerHTML = `<div class="card lp-dlg"><div class="card-h"><b>${esc(x.name)} 的联系方式</b><button class="btn ghost sm" data-x>关闭</button></div>
    ${row("手机号", c.phone)}${row("微信号", c.wechat)}${!c.phone && !c.wechat ? `<div class="empty" style="border:0">对方没有开放联系方式</div>` : ""}
    <p class="sub" style="margin:10px 0 0">获取于 ${esc(String(c.contact_at || "").slice(0, 16))}。仅用于和对方洽谈合作,请勿外传或群发营销。</p></div>`;
  m.style.zIndex = 60; document.body.appendChild(m);
  m.onclick = async e => {
    if (e.target === m || e.target.closest("[data-x]")) return m.remove();
    const cp = e.target.closest("[data-cp]"); if (cp) { try { await copyText(cp.dataset.cp); toast("已复制"); } catch { toast("复制失败,请手动选中复制", 1); } }
  };
}

const KL_SNIPS = ["已加微信", "已电话沟通", "等对方回复", "已报价", "对方要样品", "已寄样品", "意向合作", "暂无合作意向"];
function klNote(x, statuses) {
  const m = document.createElement("div"); m.className = "mask";
  const md = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(5, 10);
  m.innerHTML = `<form class="card lp-dlg kl-dlg"><div class="card-h"><b>${esc(x.name)}</b><button type="button" class="btn ghost sm" data-x>关闭</button></div>
    <p class="sub" style="margin:0 0 10px">${esc(x.history_cats.map(c => c.split("/")[0]).join("、") || "未标注历史合作")} · 粉丝 ${fmtN(x.fans)} · 首次发现 ${esc(String(x.first_seen).slice(0, 10))}${x.contacted_at ? ` · 首次联系 ${esc(String(x.contacted_at).slice(0, 10))}` : ""}</p>
    <div class="kl-two"><div><label class="f" style="margin-top:0">建联状态</label><select id="kls">${statuses.map(s => `<option value="${s.key}" ${x.status === s.key ? "selected" : ""}>${s.name}</option>`).join("")}</select></div>
      <div><label class="f" style="margin-top:0">负责人</label><input id="klo" value="${esc(x.owner || "")}" placeholder="还没人认领" readonly title="在列表里点「认领跟进」/「放手」修改"></div></div>
    <label class="f">备注 <span class="sub">沟通记录、报价、合作意向…点下面的短语可以快速插入</span></label>
    <div class="kl-snips">${KL_SNIPS.map(t => `<button type="button" class="chip" data-sn="${t}">${t}</button>`).join("")}</div>
    <textarea id="kln" rows="7" maxlength="1000" placeholder="例如:${md} 加了微信,对方说下周回复报价">${esc(x.note || "")}</textarea>
    <div class="row" style="margin-top:12px;justify-content:space-between;gap:8px"><span class="sub" id="klc">0 / 1000</span><span><button type="button" class="btn gray" data-x>取消</button> <button class="btn">保存</button></span></div></form>`;
  m.style.zIndex = 60; document.body.appendChild(m);
  const ta = m.querySelector("#kln"), cnt = () => { m.querySelector("#klc").textContent = `${ta.value.length} / 1000`; };
  cnt(); ta.oninput = cnt; ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
  m.onclick = e => {
    if (e.target === m || e.target.closest("[data-x]")) return m.remove();
    const sn = e.target.closest("[data-sn]");
    if (sn) { ta.value = `${ta.value}${ta.value && !ta.value.endsWith("\n") ? "\n" : ""}${md} ${sn.dataset.sn}`; cnt(); ta.focus(); }
  };
  ta.onkeydown = e => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") m.querySelector("form").requestSubmit(); };
  m.querySelector("form").onsubmit = async e => {
    e.preventDefault();
    try {
      const body = { note: ta.value }; if ($("#kls").value !== x.status) body.status = $("#kls").value;
      await api(`/api/kol/${x.id}`, "POST", body); toast("已保存"); m.remove(); fKol();
    } catch (err) { toast(err.message, 1); }
  };
}

window.FLOW_VIEWS.kol = { t: "达人库", d: "千帆买手广场里的合作达人:按分类、历史合作品类、量级、适配度筛选,记录建联进度", fn: fKol };
