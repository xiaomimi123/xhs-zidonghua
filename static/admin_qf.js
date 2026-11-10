// ---------- 数据看板(千帆后台数据) ----------
const QFSTAFF = () => window.QF_STAFF === true;   // 员工端(小红书流水线)里的数据看板:只读,不带同步设置和管理操作
const qfu = p => (QFSTAFF() ? "/api/qianfan/board" : "/api/admin/qianfan") + p;
const QF = { tab: "account", kind: "hot_notes", scope: "", period: "", account: "", shop: "" };
const QF_TABS_ALL = [["account", "账号数据"], ["notes", "笔记表现"], ["topics", "选题分析"], ["hot_notes", "热门笔记榜"], ["hot_topics", "热门话题榜"], ["hot_keywords", "热门关键词榜"], ["sync", "同步设置"]];
const qfTabs = () => QF_TABS_ALL.filter(t => !(QFSTAFF() && t[0] === "sync"));
const QF_PERIOD = { "": "默认", "1d": "近 1 天", "7d": "近 7 天", "30d": "近 30 天" };
const dlt = (cur, prev, k) => { if (!prev || cur?.[k] == null || prev[k] == null) return ""; const d = cur[k] - prev[k]; return d ? `<em class="qf-d ${d > 0 ? "up" : "down"}">${d > 0 ? "▲" : "▼"} ${fmt(Math.abs(d))}</em>` : ""; };

function qfSpark(series, key) {
  const v = series.map(s => s[key]).filter(x => x != null);
  if (v.length < 2) return `<div class="sub" style="padding:18px 0">数据点不够,再同步几次就能看到趋势</div>`;
  const W = 560, H = 120, lo = Math.min(...v), hi = Math.max(...v), span = hi - lo || 1;
  const pts = v.map((x, i) => [(i / (v.length - 1) * (W - 20) + 10).toFixed(1), (H - 16 - (x - lo) / span * (H - 32)).toFixed(1)]);
  return `<svg viewBox="0 0 ${W} ${H}" class="qf-spark" preserveAspectRatio="none"><polyline fill="none" stroke="var(--accent)" stroke-width="2" points="${pts.map(p => p.join(",")).join(" ")}"/>${pts.map(p => `<circle cx="${p[0]}" cy="${p[1]}" r="2.5" fill="var(--accent)"/>`).join("")}</svg><div class="qf-axis"><span>${lt(series[0].at, "d")}</span><span>最低 ${fmt(lo)} · 最高 ${fmt(hi)}</span><span>${lt(series[series.length - 1].at, "d")}</span></div>`;
}

async function vQianfan() {
  const o = await api(qfu(`/overview?account=${encodeURIComponent(QF.account)}&shop=${encodeURIComponent(QF.shop)}`));
  if (QF.shop && o.shop !== QF.shop) { QF.shop = ""; QF.account = ""; return vQianfan(); }
  if (QF.account && o.selected !== QF.account) { QF.account = ""; return vQianfan(); }
  if (o.shops.length && !QF.shop && QF.tab.startsWith("hot_") && QF.tab !== "hot_topics") { QF.shop = o.shops[0].shop_id; return vQianfan(); }   // 榜单是分店铺存的,没选就看第一个店铺
  const stat = o.status;
  $("#pa").innerHTML = QFSTAFF() ? (typeof dataSwitch === "function" ? dataSwitch("data") : "") : `<button class="btn" id="qsync">立即同步</button>`;
  if (QFSTAFF()) $("#main").onclick = null;
  const lastAt = Object.values(stat.kinds).map(k => k.last).sort().pop();
  const body = {
    account: () => qfAccount(o), notes: () => qfNotes(o), topics: () => qfTopics(o), sync: () => qfSync(o),
    hot_notes: () => qfHot(o, "hot_notes"), hot_topics: () => qfHot(o, "hot_topics"), hot_keywords: () => qfHot(o, "hot_keywords"),
  }[QF.tab];
  const stale = o.shops.filter(x => !x.online && (x.pending > 0 || !x.last_sync || (Date.now() - new Date(x.last_sync.replace(" ", "T") + "Z")) > 24 * 3600e3)), live = o.shops.filter(x => x.online);
  const shopBanner = !QFSTAFF() && o.shops.length > 1 && stale.length ? `<div class="banner warn">${stale.map(x => `「${esc(x.name || x.shop_id)}」${x.last_sync ? `最近同步 ${lt(x.last_sync)}` : "还没有同步过"}${x.pending ? `,有 ${x.pending} 个同步任务在等` : ""}`).join(";")}。${live.length ? `采集端现在登录的是「${esc(live.map(x => x.name).join("、"))}」。` : "采集端现在没有登录任何店铺。"}同一个千帆账号同一时间只能登录一个店铺:在 ego lite 里登录该店铺后,采集端会自动补采。</div>` : "";
  $("#main").innerHTML = `${shopBanner}${o.sample && !QFSTAFF() ? `<div class="banner">当前显示的是<b>示例数据</b>(用来预览页面),采集端接上以后点这里 <button class="btn gray sm" id="qclr">清除示例数据</button></div>` : ""}
  <div class="row" style="justify-content:space-between;margin-bottom:12px;gap:8px;flex-wrap:wrap"><div class="seg" id="qtabs">${qfTabs().map(([k, n]) => `<button class="${k === QF.tab ? "on" : ""}" data-t="${k}">${n}</button>`).join("")}</div>
    <span class="sub">${lastAt ? `最近同步 ${lt(lastAt)}` : "还没有同步过数据"} · 自动同步${stat.enabled ? "已开启" : "未开启"}</span></div>${["account", "notes", "topics", "hot_notes", "hot_keywords"].includes(QF.tab) ? qfShopChips(o) : ""}${["account", "notes", "topics"].includes(QF.tab) ? qfAccChips(o) : ""}<div id="qbody"></div>`;
  $("#qbody").innerHTML = await body();
  $$(".qacc button").forEach(b => b.onclick = () => { QF.account = b.dataset.a; vQianfan(); });
  $$(".qshop button").forEach(b => b.onclick = () => { QF.shop = b.dataset.s; QF.account = ""; vQianfan(); });
  $$("[data-pick]").forEach(r => r.onclick = () => { QF.account = r.dataset.pick; vQianfan(); });
  $$("#qtabs button").forEach(b => b.onclick = () => { QF.tab = b.dataset.t; if (b.dataset.t.startsWith("hot_")) { QF.kind = b.dataset.t; QF.scope = ""; QF.period = ""; } vQianfan(); });
  if ($("#qsync")) $("#qsync").onclick = async e => { e.target.disabled = true; try { const r = await api("/api/admin/qianfan/sync", "POST", { kinds: [] }); toast(r.created ? `已下发 ${r.created} 个同步任务,等采集端领取` : "已有同步任务在排队,不用重复下发"); } catch (x) { toast(x.message, 1) } e.target.disabled = false; };
  if ($("#qtag")) $("#qtag").onclick = async e => { e.target.disabled = true; try { await api("/api/admin/qianfan/topics/tag", "POST"); } catch (x) { toast(x.message, 1); } vQianfan(); };
  if ($("#qclr")) $("#qclr").onclick = async () => { await api("/api/admin/qianfan/sample", "DELETE"); vQianfan(); };
  qfBind(o);
}

function qfEmpty(msg) {
  if (QFSTAFF()) return `<section class="card"><div class="empty" style="padding:36px 20px;text-align:center"><p>${msg}</p><p class="sub">数据由采集端从千帆后台同步,有数据后这里会自动出现。</p></div></section>`;
  return `<section class="card"><div class="empty" style="padding:36px 20px;text-align:center"><p>${msg}</p><p class="sub">采集端按《千帆数据同步说明》回传数据后这里会自动出现。也可以先预览页面效果:</p><button class="btn gray" id="qsample">载入示例数据</button></div></section>`;
}

function qfShopChips(o) {
  if (o.shops.length < 2) return "";
  const hot = QF.tab.startsWith("hot_");
  return `<div class="seg qshop" style="margin-bottom:12px">${hot ? "" : `<button class="${QF.shop === "" ? "on" : ""}" data-s="">全部店铺</button>`}${o.shops.map(x => `<button class="${QF.shop === x.shop_id ? "on" : ""}" data-s="${esc(x.shop_id)}" title="${x.online ? "采集端当前登录的是这个店铺" : "采集端没有登录这个店铺"}"><span style="color:${x.online ? "var(--ok)" : "var(--sub)"}">●</span> ${esc(x.name || x.shop_id)}</button>`).join("")}</div>`;
}

function qfAccChips(o) {
  if (o.accounts.length < 2) return "";
  const cnt = a => o.note_counts?.[a] ?? 0, all = Object.values(o.note_counts || {}).reduce((x, y) => x + y, 0);
  const tag = QF.tab === "notes";
  const items = [["", `全部账号${tag ? ` · ${all}` : ""}`], ...o.accounts.map(a => [a.account_id, `${a.name || "未命名"}${tag ? ` · ${cnt(a.account_id)}` : ""}`])];
  return o.accounts.length > 4   // 账号多:用可换行的标签;少:分段按钮
    ? `<div class="fl-chips qacc" style="margin-bottom:14px">${items.map(([id, t]) => `<button class="chip ${QF.account === id ? "on" : ""}" data-a="${esc(id)}">${esc(t)}</button>`).join("")}</div>`
    : `<div class="seg qacc" style="margin-bottom:12px">${items.map(([id, t]) => `<button class="${QF.account === id ? "on" : ""}" data-a="${esc(id)}">${esc(t)}</button>`).join("")}</div>`;
}

function qfAccount(o) {
  if (!o.accounts.length) return qfEmpty("还没有账号数据");
  if (o.accounts.length > 1 && !o.selected) {
    const cols = [["fans", "粉丝"], ["views", "阅读"], ["likes", "点赞"], ["collects", "收藏"], ["comments", "评论"], ["shares", "分享"]];
    return `<section class="card flush"><div class="card-h"><b>各账号对比</b><span class="sub">点一行看这个账号的趋势和笔记 · 互动数为近 7 日普通笔记</span></div><div class="tbl-wrap"><table class="tbl"><thead><tr><th>账号</th>${o.shops.length > 1 ? "<th>店铺</th>" : ""}${cols.map(([, n]) => `<th class="num">${n}</th>`).join("")}<th>数据时间</th></tr></thead><tbody>
    ${o.accounts.map(a => `<tr data-pick="${esc(a.account_id)}" style="cursor:pointer"><td>${esc(a.name || "未命名")}${a.red_id ? ` <span class="sub">${esc(a.red_id)}</span>` : ""}</td>${o.shops.length > 1 ? `<td class="sub">${esc(a.shop_name || "")}</td>` : ""}${cols.map(([k]) => `<td class="num">${a[k] != null ? fmt(a[k]) : ""}${dlt(a, a.prev, k)}</td>`).join("")}<td>${lt(a.at)}</td></tr>`).join("")}</tbody></table></div></section>`;
  }
  const a = o.account; if (!a) return qfEmpty("还没有账号数据");
  const prev = o.series.length > 1 ? o.series[o.series.length - 2] : null;
  const cards = [["fans", "粉丝"], ["new_fans", "新增粉丝"], ["notes", "笔记数"], ["views", "阅读"], ["likes", "点赞"], ["collects", "收藏"], ["comments", "评论"], ["shares", "分享"], ["visits", "主页访客"]].filter(([k]) => a[k] != null);
  return `<div class="stats">${cards.map(([k, n]) => `<div class="card stat"><span class="sub">${n}</span><b>${fmt(a[k])}</b><div class="mini">${dlt(a, prev, k) || "&nbsp;"}</div></div>`).join("")}</div>
  <section class="card"><div class="card-h"><b>粉丝趋势</b><span class="sub">${esc(a.name || "")} · 每次同步一个点</span></div><div style="padding:6px 16px 14px">${qfSpark(o.series, "fans")}</div></section>`;
}

const QV = { "好": ["done", "表现好"], "弱": ["error", "偏弱"], "样本少": ["", "样本少"], "数据太少": ["", "数据太少"], "": ["", ""] };
async function qfTopics(o) {
  const r = await api(qfu(`/topics?account=${encodeURIComponent(QF.account)}&shop=${encodeURIComponent(QF.shop)}`));
  if (!r.total) return qfEmpty(o.selected ? "这个账号下还没有笔记数据" : "还没有笔记数据");
  const tg = r.tagging;
  if (tg.running) setTimeout(() => { if (QF.tab === "topics" && (!QFSTAFF() || state.tab === "data")) vQianfan(); }, 3000);
  const banner = r.untagged ? `<div class="banner">${tg.running ? `正在给笔记归类… ${tg.done}/${tg.total}(自动刷新)` : `还有 <b>${r.untagged}</b> 篇笔记没归类。${tg.error ? `上次失败:${esc(tg.error)} ` : ""}${QFSTAFF() ? "" : `<button class="btn gray sm" id="qtag">立即归类</button>`}`}</div>` : "";
  if (!r.base.n) return banner + qfEmpty("笔记还没归类,归类完成后这里会按类汇总");
  const byEng = r.metric === "eng", mname = byEng ? "平均互动" : "平均阅读";
  const pool = [...r.by_angle, ...r.by_type];
  const good = pool.filter(x => x.verdict === "好").sort((a, b) => b.lift - a.lift).slice(0, 3);
  const weak = pool.filter(x => x.verdict === "弱").sort((a, b) => a.lift - b.lift).slice(0, 2);
  const who = o.selected ? esc(o.accounts.find(a => a.account_id === o.selected)?.name || "") : (o.shop ? esc(o.shops.find(x => x.shop_id === o.shop)?.name || "") : "全部账号");
  const tbl = (title, rows) => `<section class="card flush" style="margin-top:16px"><div class="card-h"><b>${title}</b><span class="sub">样本够的类别在前,按${mname}从高到低</span></div><div class="tbl-wrap"><table class="tbl"><thead><tr><th>类别</th><th class="num">篇数</th><th class="num">平均阅读</th><th class="num">平均赞</th><th class="num">平均藏</th><th class="num">平均评</th><th class="num">平均互动</th><th class="num">互动率</th><th class="num">相对平均</th><th>判断</th><th>代表笔记</th></tr></thead><tbody>
    ${rows.map(x => `<tr><td>${esc(x.name)}</td><td class="num">${x.n}</td><td class="num">${byEng ? fmt(x.views_avg) : `<b>${fmt(x.views_avg)}</b>`}</td><td class="num">${fmt(x.likes_avg)}</td><td class="num">${fmt(x.collects_avg)}</td><td class="num">${fmt(x.comments_avg)}</td><td class="num">${byEng ? `<b>${fmt(x.eng_avg)}</b>` : fmt(x.eng_avg)}</td><td class="num">${x.rate != null ? (x.rate * 100).toFixed(1) + "%" : ""}</td><td class="num">${x.lift != null ? "×" + x.lift : ""}</td><td>${QV[x.verdict][1] ? `<span class="st ${QV[x.verdict][0]}">${QV[x.verdict][1]}</span>` : ""}</td><td class="sub">${esc(x.best.title).slice(0, 26)}${x.best.likes != null ? ` · 赞 ${fmt(x.best.likes)}` : ""}</td></tr>`).join("")}</tbody></table></div></section>`;
  return `${banner}<section class="card"><div class="card-h"><b>${who} · 选题表现</b><span class="sub">已归类 ${r.base.n} 篇 · 每篇平均阅读 ${fmt(r.base.views_avg)} · 平均互动 ${fmt(r.base.eng_avg)}</span></div>
    <div style="padding:6px 16px 14px;line-height:1.9">
    ${byEng ? "" : `<div class="banner warn" style="margin:0 0 8px">这个账号每篇平均互动只有 ${fmt(r.base.eng_avg)},互动数太少没有区分度,下面改用<b>阅读量</b>比较。</div>`}
    ${good.length ? `<div>表现好:${good.map(x => `<b>${esc(x.name)}</b>(${x.n} 篇,${mname} ${fmt(byEng ? x.eng_avg : x.views_avg)},是账号平均的 ${x.lift} 倍)`).join(";")}</div>` : `<div class="sub">暂时没有明显跑赢账号平均的类别(某类至少 3 篇、总量够大才下结论)</div>`}
    ${weak.length ? `<div>偏弱:${weak.map(x => `<b>${esc(x.name)}</b>(${x.n} 篇,×${x.lift})`).join(";")}</div>` : ""}
    <div class="sub">互动数是笔记累计值,发布得早的笔记会占便宜;少于 3 篇或总量太小的类别只作参考。</div></div></section>
  ${tbl("按选题角度", r.by_angle)}${tbl("按内容类型", r.by_type)}`;
}

function qfNotes(o) {
  const n = o.notes; if (!n.items.length) return qfEmpty(o.selected ? "这个账号下还没有笔记表现数据" : "还没有笔记表现数据");
  const multi = o.accounts.length > 1;
  const cols = [["views", "浏览"], ["likes", "点赞"], ["collects", "收藏"], ["comments", "评论"], ["shares", "分享"], ["new_fans", "涨粉"]].filter(([k]) => n.items.some(i => i[k] != null));   // 千帆没给的列不显示,不要一排 0
  const cell = v => v != null ? fmt(v) : `<span class="na">-</span>`;
  return `<section class="card flush"><div class="card-h"><b>我们发布的笔记</b><span class="sub">共 ${n.total} 篇${n.items.length < n.total ? `(按互动量显示前 ${n.items.length} 篇)` : ""},其中 ${n.matched} 篇对上了工作台里生成的稿子 · 数据时间 ${lt(n.at)}</span></div><div class="tbl-wrap"><table class="tbl"><thead><tr><th>标题</th>${multi ? "<th>账号</th>" : ""}${cols.map(([, t]) => `<th class="num">${t}</th>`).join("")}<th>发布</th></tr></thead><tbody>
  ${n.items.map(i => `<tr><td class="tt" title="${esc(i.title)}">${i.mine ? `<span class="st done" title="工作台生成的稿子">自产</span> ` : ""}${esc(i.title)}</td>${multi ? `<td class="sub">${esc(i.account_name || "未归属")}</td>` : ""}${cols.map(([k]) => `<td class="num">${cell(i[k])}</td>`).join("")}<td class="nowrap">${esc((i.published_at || "").slice(0, 10))}</td></tr>`).join("")}</tbody></table></div></section>`;
}

async function qfHot(o, kind) {
  const sc = o.scopes[kind] || [];
  if (!sc.length) return qfEmpty("还没有这个榜单的数据");
  const scopes = [...new Set(sc.map(s => s.scope))];
  if (!scopes.includes(QF.scope)) QF.scope = scopes[0];
  const periods = sc.filter(s => s.scope === QF.scope).map(s => s.period);
  if (!periods.includes(QF.period)) QF.period = periods[0] ?? "";
  const r = await api(qfu(`/hot?kind=${kind}&scope=${encodeURIComponent(QF.scope)}&period=${QF.period}&shop=${encodeURIComponent(QF.shop)}`));
  const isNote = kind === "hot_notes", name = i => isNote ? i.title : i.name;
  const sel = (id, opts, cur, label) => opts.length > 1 || (opts[0] ?? "") !== "" ? `<select id="${id}" class="sm">${opts.map(v => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(label(v))}</option>`).join("")}</select>` : "";
  const act = isNote ? (QFSTAFF() ? "" : `<button class="btn gray sm" id="qtt">前 10 条加入沉淀任务</button>`) : `<button class="btn gray sm" id="qtk">复制前 10 个词</button>`;
  const has = k => r.items.some(i => i[k] != null);
  const cols = (isNote ? [["likes", "点赞"], ["collects", "收藏"], ["comments", "评论"]] : [["notes", "笔记数"]]).filter(([k]) => has(k));
  const heatName = isNote && !has("likes") ? "热度(阅读)" : kind === "hot_keywords" ? "热度(搜索)" : kind === "hot_topics" ? "热度(浏览)" : "热度";
  return `<section class="card flush"><div class="card-h"><b>${qfTabs().find(t => t[0] === kind)[1]}</b>${sel("qscope", scopes, QF.scope, v => v || "全站")}${sel("qper", periods, QF.period, v => QF_PERIOD[v] || v)}<span class="sub">数据时间 ${lt(r.at)}</span>${act}</div>
  <div class="tbl-wrap"><table class="tbl"><thead><tr><th style="width:48px">#</th><th>${isNote ? "标题" : kind === "hot_topics" ? "话题" : "关键词"}</th>${isNote ? "<th>作者</th>" : ""}${cols.map(([, t]) => `<th class="num">${t}</th>`).join("")}${has("heat") ? `<th class="num">${heatName}</th>` : ""}</tr></thead><tbody>
  ${r.items.map(i => `<tr><td class="sub">${i.rank}</td><td class="tt" title="${esc(name(i))}">${esc(name(i))}</td>${isNote ? `<td class="sub nowrap">${esc(i.author || "")}</td>` : ""}${cols.map(([k]) => `<td class="num">${i[k] != null ? fmt(i[k]) : `<span class="na">-</span>`}</td>`).join("")}${has("heat") ? `<td class="num">${i.heat != null ? fmt(i.heat) : ""}</td>` : ""}</tr>`).join("")}</tbody></table></div></section>`;
}

function qfSync(o) {
  const s = o.settings, ks = o.status.kinds, on = k => (s.qf_kinds || "").split(",").includes(k);
  const tst = v => typeof st === "function" ? st(v) : v;
  return `<section class="card"><div class="card-h"><b>自动同步</b><span class="sub">采集端空闲时领取,排在用户任务之后、笔记库沉淀任务之前</span></div><form id="qfform" style="padding:8px 16px 16px">
    <div class="fld"><label><input type="checkbox" id="qen" ${s.qf_enabled === "1" ? "checked" : ""}> 开启自动同步</label></div>
    <div class="fld"><label>同步哪些数据</label><div class="row" style="gap:14px;flex-wrap:wrap">${Object.entries(ks).map(([k, v]) => `<label><input type="checkbox" class="qk" value="${k}" ${on(k) ? "checked" : ""}> ${v.name}</label>`).join("")}</div></div>
    <div class="row" style="gap:12px;flex-wrap:wrap"><div class="fld" style="flex:1;min-width:150px"><label>账号 / 笔记表现间隔(小时)</label><input id="qah" type="number" min="1" value="${esc(s.qf_account_hours)}"></div>
    <div class="fld" style="flex:1;min-width:150px"><label>热门榜单间隔(小时)</label><input id="qhh" type="number" min="1" value="${esc(s.qf_hot_hours)}"></div></div>
    <div class="fld"><label>榜单品类(逗号分隔,留空 = 全站)</label><input id="qcat" value="${esc(s.qf_categories)}" placeholder="如:美食,穿搭,美妆"></div>
    <div class="fld"><label>榜单周期(逗号分隔)</label><input id="qper2" value="${esc(s.qf_periods)}" placeholder="1d,7d,30d"></div>
    <button class="btn">保存</button></form></section>
  <section class="card" style="margin-top:16px"><div class="card-h"><b>各类数据状态</b></div><div class="tbl-wrap"><table class="tbl"><thead><tr><th>数据</th><th>最近同步</th><th>最近任务</th><th>说明</th></tr></thead><tbody>
  ${Object.entries(ks).map(([k, v]) => `<tr><td>${v.name}</td><td>${v.last ? lt(v.last) : "—"}</td><td>${v.task_status ? tst(v.task_status) : "—"}</td><td class="sub">${esc(v.error)}</td></tr>`).join("")}</tbody></table></div></section>
  <section class="card" style="margin-top:16px"><div class="card-h"><b>手动导入</b><span class="sub">采集端还没接上时,把数据整理成 JSON 粘贴进来(格式见《千帆数据同步说明》)</span></div><div style="padding:8px 16px 16px">
    <textarea id="qjson" rows="6" style="width:100%;font-family:monospace;font-size:12px" placeholder='{"kind":"hot_topics","period":"7d","items":[{"rank":1,"name":"早餐打卡","heat":98000}]}'></textarea>
    <div class="row" style="margin-top:8px;gap:8px"><button class="btn gray" id="qimp">导入</button><button class="btn ghost" id="qsample2">载入示例数据</button></div></div></section>`;
}

function qfBind(o) {
  const smp = async () => { await api("/api/admin/qianfan/sample", "POST"); toast("已载入示例数据"); vQianfan(); };
  ["qsample", "qsample2"].forEach(id => { if ($("#" + id)) $("#" + id).onclick = smp; });
  if ($("#qscope")) $("#qscope").onchange = e => { QF.scope = e.target.value; QF.period = ""; vQianfan(); };
  if ($("#qper")) $("#qper").onchange = e => { QF.period = e.target.value; vQianfan(); };
  if ($("#qtt")) $("#qtt").onclick = async () => {
    const r = await api(qfu(`/hot?kind=hot_notes&scope=${encodeURIComponent(QF.scope)}&period=${QF.period}&shop=${encodeURIComponent(QF.shop)}`));
    const x = await api("/api/admin/qianfan/to_title_tasks", "POST", { titles: r.items.slice(0, 10).map(i => i.title) }); toast(`已新建 ${x.created} 个沉淀任务`);
  };
  if ($("#qtk")) $("#qtk").onclick = async () => {
    const r = await api(qfu(`/hot?kind=${QF.kind}&scope=${encodeURIComponent(QF.scope)}&period=${QF.period}&shop=${encodeURIComponent(QF.shop)}`));
    const t = r.items.slice(0, 10).map(i => i.name).join("、"); try { await copyText(t); toast("已复制:" + t); } catch { toast(t); }
  };
  if ($("#qfform")) $("#qfform").onsubmit = async e => {
    e.preventDefault();
    try {
      await api("/api/admin/qianfan/settings", "POST", { qf_enabled: $("#qen").checked ? "1" : "0", qf_kinds: $$(".qk:checked").map(x => x.value).join(","), qf_account_hours: $("#qah").value, qf_hot_hours: $("#qhh").value, qf_categories: $("#qcat").value, qf_periods: $("#qper2").value });
      toast("已保存"); vQianfan();
    } catch (x) { toast(x.message, 1); }
  };
  if ($("#qimp")) $("#qimp").onclick = async () => {
    let b; try { b = JSON.parse($("#qjson").value); } catch { return toast("JSON 格式不对", 1); }
    try { const r = await api("/api/admin/qianfan/import", "POST", b); toast(`已导入 ${r.stored} 条`); vQianfan(); } catch (x) { toast(x.message, 1); }
  };
}
