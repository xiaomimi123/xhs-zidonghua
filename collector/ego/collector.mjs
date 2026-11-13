// 千帆数据采集端(在 ego lite 里运行,复用你已登录的千帆)。协议:docs/采集端_千帆数据同步说明.md
// 运行:ego-browser nodejs -e "await import('file:///D:/小红书自动化批量工具/collector/ego/run.mjs')"
// 演练(不连工作台,只打印会回传的数据):run-dry.mjs
const fs = await import("node:fs/promises");
const DRY = globalThis.QF_DRY === true;
const HERE = "D:/小红书自动化批量工具/collector";
const CFG = globalThis.QF_CFG || {};   // 多店铺:每个店铺一个实例(各自的 ego lite 窗口 / 登录),由 run-xxx.mjs 传入名字、日志、能做的类别
const SPACE = CFG.space || "qianfan-collector";
const ARK = "https://ark.xiaohongshu.com";

// ---------- 配置 ----------
const env = {};
try {
  for (const l of (await fs.readFile(`${HERE}/.env`, "utf8")).split(/\r?\n/)) {
    const m = l.match(/^\s*([A-Z_]+)\s*=\s*(.*?)\s*$/);
    if (m && !l.trim().startsWith("#")) env[m[1]] = m[2].replace(/^"|"$/g, "");
  }
} catch {}
const SERVER = env.XHS_SERVER || "http://127.0.0.1:18765";
const TOKEN = env.XHS_TOKEN || "";
const CV = 4;   // collector version: bump when adding a task kind (e.g. buyers); server setting collector_min_cv makes older versions exit
const WORKER = CFG.worker || env.XHS_WORKER || "ego-qianfan";
const KINDS = CFG.kinds || env.XHS_KINDS || "";   // 这个采集端能做的千帆数据类别,空 = 都能做
const LOG = CFG.log || `${HERE}/ego/run.log`;
const log = (...a) => {
  const line = [new Date().toTimeString().slice(0, 8), ...a].join(" ");
  console.log(line);
  fs.appendFile(LOG, line + "\n", "utf8").catch(() => {});   // ego-browser 要等进程结束才显示输出,长跑时看这个文件
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class QfError extends Error {}

// ---------- 工作台接口 ----------
async function api(path, opt = {}) {
  const r = await fetch(SERVER + path, { ...opt, headers: { Authorization: `Bearer ${TOKEN}`, "content-type": "application/json" } });
  if (r.status === 401) throw new Error("Token 无效(401),检查 collector/.env 的 XHS_TOKEN");
  return r;
}
const ping = async () => (await api(`/api/ping?worker=${WORKER}`)).json();

// ---------- 千帆页面里的请求 ----------
let page;
async function ensureArk() {
  const url = await page.url();
  if (/login|passport/.test(url)) throw new QfError("千帆登录已过期,需要重新登录(在 ego lite 里登录后重试)");
  if (!url.startsWith(ARK) || /app-system\/login|ark\/login/.test(url)) {
    await page.goto(`${ARK}/app-note/hot-word/v2`);
    await page.waitForTimeout(3000);
    if (/login|passport|choose|select/.test(await page.url())) throw new QfError("千帆登录已过期或未选择店铺,需要在 ego lite 里重新登录并进入店铺");
  }
}
async function qf(path, body) {
  const r = await page.fetch(path, body === undefined ? {} : { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (r.status === 401 || r.status === 403) throw new QfError("千帆登录已过期,需要重新登录");
  let j;
  try { j = JSON.parse(r.body); } catch { throw new QfError(`千帆接口返回非 JSON(${r.status}):${path}`); }
  if (j.success === false || (j.code && ![0, 200].includes(j.code))) throw new QfError(`千帆接口报错:${path} ${j.msg || j.code}`);
  return j.data;
}

// 读联系方式用的独立标签页(不占采集达人列表用的 p1)
let kpage;
async function contactPage() {
  if (kpage) { try { await kpage.url(); return kpage; } catch { kpage = null; } }
  kpage = await task.newPage();
  return kpage;
}

// 创作服务平台(creator.xiaohongshu.com)和千帆不同源,page.fetch 只能请求当前页面同源的地址,所以单开一个标签页
let cpage;
async function creatorPage() {
  if (cpage) return cpage;
  const tab = (await task.tabs()).find((t) => /creator\.xiaohongshu\.com/.test(t.url));
  cpage = tab ? (tab.label ? task.page(tab.label) : await task.adopt(tab.page)) : await task.newPage();
  if (!/creator\.xiaohongshu\.com/.test(await cpage.url())) {
    await cpage.goto("https://creator.xiaohongshu.com/creator/inspiration");
    await cpage.waitForTimeout(3000);
  }
  if (/login/.test(await cpage.url())) { cpage = null; throw new QfError("创作服务平台未登录,需要在 ego lite 里登录创作服务平台"); }
  return cpage;
}
async function qfCreator(path) {
  const r = await (await creatorPage()).fetch(path);
  if (r.status === 401 || r.status === 403) { cpage = null; throw new QfError("创作服务平台登录已过期,需要重新登录"); }
  let j;
  try { j = JSON.parse(r.body); } catch { throw new QfError(`创作服务平台返回非 JSON(${r.status})`); }
  if (j.success === false || (j.code && ![0, 200].includes(j.code))) throw new QfError(`创作服务平台接口报错:${path} ${j.msg || j.code}`);
  return j.data;
}

// 千帆数据中心的统一数据接口:POST butterfly/data,一次一个 block。返回该 block({count, data:[...行]})
async function butterfly(blockKey, filterMap, extra = {}) {
  const d = await qf(`/api/edith/butterfly/data?type=${blockKey}`, { requestBody: { blockElements: [{ blockKey, filterMap, ...extra }] } });
  return (d || [])[0] || {};
}

// 笔记 ID → 所属账号。商品笔记、普通笔记的数据列表每一行都带 userId / nickName
async function noteAccountMap() {
  const map = new Map();
  for (const [key, nt] of [["arkSellerNoteGoodsNoteDataList", 2], ["arkSellerNoteNormalNoteDataList", 1]]) {
    for (let page = 1; page <= 15; page++) {
      const b = await butterfly(key, { dateSelectType: "recent_30", dateType: 3, publishTimeWindowCode: 0 }, { orderBy: [{ field: "noteReadCnt", orderBy: "desc" }], page, size: 100 });
      const rows = b.data || [];
      for (const r of rows) {
        const id = r.noteId?.value, uid = r.userId?.value;
        if (id && uid && !map.has(id)) map.set(id, { account_id: uid, account_name: r.nickName?.value || "" });
      }
      if (rows.length < 100) break;
      await sleep(500 + Math.random() * 500);
    }
  }
  return map;
}

// ---------- 店铺:这个采集端登录的是哪个店铺 ----------
let shop = { id: "", name: "" };
async function identifyShop() {
  await ensureArk();
  const d = await qf("/api/edith/seller/info/v2");
  shop = { id: d.seller_id || "", name: d.seller_name || d.name || "" };
  if (!shop.id) throw new QfError("读不到店铺信息");
}

// ---------- 各类任务 ----------
const handlers = {
  // 笔记管理 → 商品笔记 + 普通笔记(浏览/点赞/收藏/评论/分享),按 note_id 去重,并标上所属账号
  async notes() {
    const byId = new Map();
    const add = (n) => {
      if (!n.note_id || !n.note_title || byId.has(n.note_id)) return;
      const it = { note_id: n.note_id, title: n.note_title, views: n.browse_num, likes: n.like_num, collects: n.collect_num, comments: n.comment_num, shares: n.share_num };
      if (n.publish_time) it.published_at = new Date(n.publish_time + 8 * 3600e3).toISOString().slice(0, 10);   // 毫秒时间戳,按北京时间取日期
      byId.set(n.note_id, it);
    };
    const pull = async (get, key) => {
      let total = Infinity, seen = 0;
      for (let p = 1; p <= 15 && seen < total; p++) {
        const d = await get(p);
        const list = d[key] || [];
        if (!list.length) break;
        total = d.total ?? total; seen += list.length;
        list.forEach(add);
        await sleep(600 + Math.random() * 600);
      }
    };
    await pull((p) => qf(`/api/edith/goods-note/list?page_num=${p}&page_size=20`), "note_infos");
    await pull((p) => qf("/api/edith/normal_note/list", { page_num: p, page_size: 20 }), "note_list");
    if (!byId.size) throw new QfError("笔记管理里没有笔记");
    const owner = await noteAccountMap();   // 取不到也不影响笔记表现,只是没有账号归属
    for (const [id, it] of byId) Object.assign(it, owner.get(id) || {});
    // 超过 1000 条时保留最近发布的
    const items = [...byId.values()].sort((a, b) => (b.published_at || "").localeCompare(a.published_at || ""));
    return { items: items.slice(0, 1000) };
  },

  // 搜索运营 → 热搜词(近 7 日搜索量)。category 按一级类目名包含匹配;千帆这页只有近 7 日,period 原样带回
  async hot_keywords(params) {
    const cat = (params.category || "").trim(), items = [];
    for (let p = 1; p <= 10 && items.length < 50; p++) {
      const d = await qf("/api/edith/keyword/list/v2", { pageSource: 5, firstCategoryId: "", secondCategoryIdList: [], onlyRelatedItemOwn: false, queryGroup: 5, pageNo: p, pageSize: 20, sortOrders: [] });
      for (const k of d.dataList || []) {
        if (cat && !(k.firstCategoryName || "").includes(cat) && !(k.secondCategoryName || "").includes(cat)) continue;
        items.push({ keyword: k.keyword, heat: k.searchQv7d });
      }
      if (!d.dataList || d.dataList.length < 20) break;
      await sleep(600 + Math.random() * 600);
    }
    if (!items.length) throw new QfError(cat ? `热搜词里没有「${cat}」类目的词(千帆只提供本店所在类目)` : "热搜词列表为空");
    items.sort((a, b) => b.heat - a.heat);
    return { items: items.slice(0, 50).map((x, i) => ({ rank: i + 1, ...x })) };
  },

  // 店铺下所有自营账号:每个账号一份 昵称/小红书号/粉丝 + 普通笔记近 7 日互动汇总(不含商品笔记,口径不同不混算)
  async account() {
    const list = await butterfly("arkSellerNoteNormalAccountList", { dateType: 3 });
    const accounts = [];
    for (const r of list.data || []) {
      const u = (r.userBasic?.value || [])[0];
      if (!u || !u.userId || u.userId === "ALL") continue;
      const acc = { account_id: u.userId, name: u.nickName || "", red_id: u.redId || "", fans: u.fansNum };
      const core = ((await butterfly("arkSellerNoteNormalNoteCoreOverall", { dateSelectType: "recent_7", dateType: "2", accountUserId: u.userId, noteType: "1", publishTimeWindowCode: "0" })).data || [])[0] || {};
      for (const [k, f] of [["views", "noteReadCnt"], ["likes", "noteLikeCnt"], ["collects", "noteFavCnt"], ["comments", "noteCmtCnt"], ["shares", "noteShareCnt"]])
        if (core[f] && core[f].value != null) acc[k] = core[f].value;
      accounts.push(acc);
      await sleep(400 + Math.random() * 400);
    }
    if (!accounts.length) throw new QfError("没取到店铺下的账号列表,可能页面接口变了");
    return { accounts };
  },

  // 笔记创作页的「同行笔记榜 · 阅读榜」(本店同类目的商品笔记)。千帆只给阅读数区间(如 >55k),取下限当热度;不传点赞/收藏/评论
  async hot_notes() {
    const parse = (v) => {
      const m = String(v || "").match(/([\d.]+)\s*(w|万|k|千)?/i);
      return m ? Math.round(parseFloat(m[1]) * ({ w: 1e4, 万: 1e4, k: 1e3, 千: 1e3 }[(m[2] || "").toLowerCase()] || 1)) : null;
    };
    const items = [];
    for (let p = 1; p <= 2 && items.length < 50; p++) {
      const d = await qf("/api/edith/note/query_peer_note_rank", { goods_note_flag: 1, peer_rank_type: 2, note_type: 1, query: { page_num: p, page_size: 30 } });
      for (const n of d.data_list || []) {
        if (!n.note_title) continue;
        const it = { rank: items.length + 1, title: n.note_title, author: n.user_nickname || "" };
        const heat = parse(((n.metrics || [])[0] || {}).metric_value);
        if (heat != null) it.heat = heat;
        items.push(it);
      }
      if (!d.data_list || d.data_list.length < 30) break;
      await sleep(600 + Math.random() * 600);
    }
    if (!items.length) throw new QfError("同行笔记榜为空");
    return { items: items.slice(0, 50) };
  },
  // 买手广场(只读列表):合作达人的公开信息,供达人库筛选和建联。不点「发起邀约」,不取手机号 / 微信号(接口里的 telephone 是加密串,这里根本不取)。
  // category = 合作品类关键词,按买手广场的一级品类名「包含」匹配(如「保健食品」→「保健食品/膳食营养补充食品」);留空 = 不筛选(全站 500 万人,别这样用)
  async buyers(params) {
    const cat = (params.category || "").trim(), maxPages = Math.max(1, Math.min(+params.pages || 30, 100));
    const BASE = "/app_api/edith/distribution/distributor-square";
    let firsts = [];
    if (cat) {
      const tags = await qf(`${BASE}/distributors-tags?types=distribution_category`);
      const all = ((tags.distributor_tag_map || {}).distribution_category || []).map((x) => x.first_category);
      firsts = all.filter((n) => n === cat || n.includes(cat) || cat.includes(n));
      if (!firsts.length) throw new QfError(`买手广场的合作品类里没有「${cat}」`);
    }
    const md = (ms) => (ms ? new Date(ms + 8 * 3600e3).toISOString().slice(5, 10) : "");
    const flat = (x) => {
      const i = x.distributor_data_info || {}, e = x.distributor_extra_info || {}, lp = e.live_plan || {};
      const strs = (a) => (Array.isArray(a) ? a.map((v) => (typeof v === "string" ? v : v?.first_category || v?.name || "")).filter(Boolean) : []);
      return {
        creator_id: i.distributor_id, name: i.distributor_name, avatar: i.avatar, city: i.city, level: e.grade ? `V${e.grade}` : "", fans: i.fans_num,
        categories: [...strs(i.content_categorys), ...strs(i.content_category)], history_cats: strs(i.distribution_category), willing: strs(e.willing_category), coop_tags: (i.buyer_tag || []).map(String),
        live_plan: lp.title ? `${md(lp.start_time)} ${lp.title}`.trim() : "", gmv: i.avg_sale_amount, price: i.atv, brand_gmv: i.cooperative_brand_agmv, live_days: i.live_day_cnt,
        avg_viewers: i.avg_live_viewer_num, note_read_median: i.note_read_median_num, active_rate: i.active_fans_rate, notes_coop: i.bcoo_order_cnt, red_id: i.red_id, sex: i.sex,
        url: i.distributor_id ? `https://www.xiaohongshu.com/user/profile/${i.distributor_id}` : "",
      };
    };
    const byId = new Map();
    let seed = 1000 + Math.floor(Math.random() * 9000), lastPage = maxPages;
    for (let p = 1; p <= lastPage; p++) {
      const d = await qf(`${BASE}/live/buyers`, { page_size: 20, ...(firsts.length ? { live_first_category: firsts } : {}), query_param: { page: p, limit: 20, seed } });
      seed = d.seed || seed;
      const list = d.distributor_info_list || [];
      for (const x of list) { const it = flat(x); if (it.creator_id && !byId.has(it.creator_id)) byId.set(it.creator_id, it); }
      lastPage = Math.min(maxPages, Math.ceil((d.total || 0) / 20) || maxPages);   // 一页可能不满 20 条(接口会去重),不能拿「少于 20 条」当最后一页
      if (!list.length || p >= lastPage) break;
      await sleep(900 + Math.random() * 700);
    }
    if (!byId.size) throw new QfError(cat ? `买手广场里「${cat}」没有达人` : "买手广场列表为空");
    return { items: [...byId.values()] };
  },
  // 读取一位达人的联系方式:员工在达人库里点了「获取联系方式」才会下发(每天有上限)。和人手一样操作:
  // 昵称找人 → 点那一行的「发起邀约」只为打开弹窗 → 点手机号 / 微信号旁边的眼睛 → 读文字 → 点「取消」关掉。
  // 绝不点弹窗里的「发起邀约」提交按钮(那才是真的发邀约)。弹窗里的博主名称和要找的人对不上就放弃,不读。
  async buyer_contact(params) {
    const name = (params.name || "").trim(), fans = +params.fans || 0;
    if (!name) throw new QfError("缺少达人昵称");
    const pg = await contactPage();
    // 页面出现风控 / 频率限制 / 额度用完 / 验证码一类提示 → 马上停,报「风控:…」,服务端会自动暂停获取联系方式
    const RISK = "操作过于频繁|访问过于频繁|请求过于频繁|频繁|稍后再试|异常操作|安全验证|滑块|请完成验证|验证码|账号.{0,6}(限制|风险|异常)|暂无权限|没有权限|额度.{0,4}(用完|不足)|次数.{0,6}(上限|用完|不足)|已达.{0,6}上限";
    const checkRisk = async (where) => {
      const hit = await pg.evaluate((re) => { const m = document.body.innerText.match(new RegExp(re)); return m ? m[0] : ""; }, RISK);
      const u = await pg.url();
      if (hit || /captcha|verify|passport|login/.test(u)) throw new QfError(`风控:${where}页面出现「${hit || u.slice(0, 60)}」`);
    };
    await pg.goto(`${ARK}/app-distribution/live-broadcast/kol`);
    await pg.waitForTimeout(5000);
    await checkRisk("打开买手广场后");
    const box = await pg.evaluate(() => { const i = [...document.querySelectorAll("input")].find((e) => /昵称/.test(e.placeholder || "") && e.getBoundingClientRect().width > 100); if (!i) return null; const r = i.getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }; });
    if (!box) throw new QfError("买手广场页面里找不到「昵称找人」输入框,页面可能改版了");
    await pg.mouse.click(box.x, box.y, { label: "点昵称搜索框" });
    await pg.keyboard.type(name);
    await pg.keyboard.press("Enter");
    await pg.waitForTimeout(4000);
    await checkRisk("搜索后");
    const fmt = [fans >= 10000 ? `${(fans / 10000).toFixed(1)}万` : "", fans >= 10000 ? `${Math.round(fans / 10000)}万` : "", fans ? fans.toLocaleString("en-US") : "", String(fans)].filter(Boolean);
    const target = await pg.evaluate(({ name, fmt }) => {
      const out = [];
      for (const b of [...document.querySelectorAll("button")].filter((e) => e.textContent.trim() === "发起邀约" && e.getBoundingClientRect().y > 200)) {
        let el = b, row = null;
        for (let i = 0; i < 10 && el; i++, el = el.parentElement) { const t = el.textContent || ""; if (t.includes(name) && el.querySelectorAll("button").length <= 3) { row = el; } if (t.includes(name) && el.querySelectorAll("button").length > 3) break; }
        if (!row) continue;
        const t = row.textContent;
        const r = b.getBoundingClientRect();
        out.push({ x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), fansOk: fmt.some((f) => t.includes(f)) });
      }
      return out;
    }, { name, fmt });
    const pick = target.find((t) => t.fansOk) || (target.length === 1 ? target[0] : null);
    if (!pick) throw new QfError(target.length ? `搜到 ${target.length} 位同名达人,粉丝数对不上,无法确定是哪一位,请在千帆里手动查看` : `买手广场里搜不到「${name}」`);
    await pg.mouse.click(pick.x, pick.y, { label: "打开邀约弹窗(只读联系方式,不提交)" });
    await pg.waitForTimeout(3000);
    await checkRisk("打开邀约弹窗后");
    const read = async () => pg.evaluate(() => {
      const leaf = (txt) => [...document.querySelectorAll("span, div")].find((e) => e.children.length === 0 && e.textContent.trim() === txt);
      const dlgOpen = document.body.innerText.includes("你可直接通过以下方式联系博主合作");
      const who = leaf("博主名称"); const phone = leaf("手机号"), wx = leaf("微信号");
      const rowText = (l) => (l ? l.parentElement.textContent.trim() : "");
      return { dlgOpen, who: who ? who.parentElement.textContent.replace("博主名称", "").trim() : "", phone: rowText(phone).replace("手机号", "").trim(), wechat: rowText(wx).replace("微信号", "").trim(), has: { phone: !!phone, wx: !!wx } };
    });
    const closeDialog = async () => {
      const c = await pg.evaluate(() => { const b = [...document.querySelectorAll("button, span, div")].find((e) => e.children.length <= 1 && e.textContent.trim() === "取消"); if (!b) return null; const r = b.getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }; });
      if (c) await pg.mouse.click(c.x, c.y, { label: "关闭弹窗(取消)" });
      await pg.waitForTimeout(800);
      if ((await read()).dlgOpen) await pg.keyboard.press("Escape");
    };
    let r0 = await read();
    try {
      if (!r0.dlgOpen) throw new QfError("没有打开邀约弹窗(这位达人可能不能邀约)");
      if (r0.who && !r0.who.includes(name.slice(0, 6))) throw new QfError(`弹窗里的博主是「${r0.who}」,和要找的「${name}」对不上,已放弃`);
      for (const label of ["手机号", "微信号"]) {   // 点各自旁边的眼睛(从标签往右探测第一个图标)
        const eye = await pg.evaluate((label) => {
          const l = [...document.querySelectorAll("span, div")].find((e) => e.children.length === 0 && e.textContent.trim() === label);
          if (!l) return null;
          const r = l.getBoundingClientRect(), y = Math.round(r.y + r.height / 2);
          for (let x = Math.round(r.right) + 60; x < Math.round(r.right) + 240; x += 4) { const el = document.elementFromPoint(x, y); if (el && /^(svg|path|use)$/i.test(el.tagName)) return { x, y }; }
          return null;
        }, label);
        if (eye) { await pg.mouse.click(eye.x, eye.y, { label: `点眼睛显示${label}` }); await pg.waitForTimeout(1200); }
      }
      await checkRisk("点眼睛后");
      const r1 = await read();
      const phone = (r1.phone.match(/1\d{10}/) || [""])[0];
      const wechat = /\*/.test(r1.wechat) ? "" : r1.wechat;
      return { creator_id: params.creator_id, phone, wechat, kind: "buyer_contact" };
    } finally {
      await closeDialog();
      await sleep(6000 + Math.random() * 6000);   // 两位之间隔一会儿,别连着点
    }
  },
  // 创作服务平台 → 笔记灵感 → 经典话题(按类目分,浏览量当热度)。这是平台编辑精选的话题,不是实时热度;只读页面,不碰发布页
  async hot_topics(params) {
    const cat = (params.category || "").trim();
    const d = await qfCreator("/api/galaxy/creator/select/topic/detail");
    const items = [];
    for (const g of d || []) {
      if (cat && !(g.labelName || "").includes(cat)) continue;
      for (const t of g.selectTopics || []) if (t.title) items.push({ name: t.title, heat: t.viewNum });
    }
    if (!items.length) throw new QfError(cat ? `经典话题里没有「${cat}」类目` : "经典话题为空");
    items.sort((a, b) => (b.heat || 0) - (a.heat || 0));
    return { items: items.slice(0, 50).map((x, i) => ({ rank: i + 1, ...x })) };
  },
};

async function runTask(t) {
  const h = handlers[t.query];
  if (!h) throw new QfError(`不支持的千帆任务 ${t.query}`);
  await ensureArk();
  const params = t.params || {};
  const body = await h(params);
  body.kind = t.query;
  if (t.query === "buyers") body.category = params.category || "";
  if (t.query.startsWith("hot_")) { body.period = params.period || "7d"; body.category = params.category || ""; }
  return body;
}

// ---------- 主流程 ----------
const task = await taskSpace(SPACE);
page = task.page("p1");

if (DRY) {
  for (const [query, params] of (globalThis.QF_DRY_TASKS || [["notes", {}], ["hot_keywords", { period: "7d", category: "" }], ["hot_keywords", { period: "7d", category: "零食" }], ["account", {}]])) {
    try {
      const b = await runTask({ query, params });
      if (globalThis.QF_DRY_OUT) await fs.writeFile(globalThis.QF_DRY_OUT, JSON.stringify(b), "utf8");
      log("DRY", query, JSON.stringify(params), `→ ${b.items ? b.items.length + " 条" : ""}`, JSON.stringify(b).slice(0, 500));
    } catch (e) { log("DRY", query, "失败:", e.message); }
  }
} else {
  if (!TOKEN) throw new Error("缺少 Token:在 collector/.env 里填 XHS_TOKEN");
  log("自检通过:", JSON.stringify(await ping()));
  setInterval(() => ping().catch((e) => log("心跳失败:", e.message)), 45000);
  log(`采集端 ${WORKER} 已启动,轮询 ${SERVER}`);
  for (;;) {
    try {   // 每轮都确认自己服务的是哪个店铺(窗口里切换了店铺会马上发现);读不到(没登录)就等着,不领任务
      const before = shop.id;
      await identifyShop();
      if (shop.id !== before) log(`店铺:${shop.name}(${shop.id})${before ? ` ← 之前是 ${before},窗口里切换了店铺` : ""}`);
    } catch (e) {
      if (shop.id) log("读取店铺失败,暂停领任务:", String(e.message).slice(0, 120));
      else if (!globalThis.__shopWarned) { globalThis.__shopWarned = true; log("读取店铺失败:", String(e.message).slice(0, 120)); }
      shop = { id: "", name: "" };
      await sleep(15000); continue;
    }
    let tasks = [];
    try {
      const r = await api(`/api/tasks?limit=1&worker=${WORKER}&types=qianfan&cv=${CV}&shop=${encodeURIComponent(shop.id)}&shop_name=${encodeURIComponent(shop.name)}${KINDS ? `&kinds=${encodeURIComponent(KINDS)}` : ""}`);
      tasks = (await r.json()).tasks || [];
    } catch (e) {
      if (String(e.message).includes("401")) throw e;
      log("拉取任务失败:", e.message);
    }
    for (const t of tasks) {
      log(`领到任务 #${t.id} [${t.type}] ${t.query} ${JSON.stringify(t.params || {})}`);
      let body;
      try {
        if (t.params?.shop && t.params.shop !== shop.id) throw new QfError(`任务属于另一个店铺(${t.params.shop}),这个采集端当前登录的是 ${shop.name}`);
        body = await runTask(t);
      }
      catch (e) { body = { kind: t.query, error: String(e.message).slice(0, 200), ...(t.params?.creator_id ? { creator_id: t.params.creator_id } : {}) }; log("  失败:", body.error); }
      body.task_id = t.id;
      body.shop_id = shop.id;
      body.shop_name = shop.name;
      const shown = t.query === "buyer_contact" ? { ...body, phone: body.phone ? `${body.phone.slice(0, 3)}****${body.phone.slice(-2)}` : "", wechat: body.wechat ? "***" : "" } : body;   // 日志里不留联系方式明文
      log("  回传内容:", JSON.stringify(shown).slice(0, 300));
      // 回传失败(服务重启、网络断了)不能直接丢:已经读到的结果丢了就白做了,服务端那边的任务也会一直等。
      // 重试几次(间隔 3、6、12、24、48 秒,够服务重启一轮);4xx 是内容有问题,重试没用。
      let sent = false;
      for (let k = 0; k < 6 && !sent; k++) {
        try {
          const r = await api("/api/qianfan/ingest", { method: "POST", body: JSON.stringify(body) });
          log("  回传:", r.status, (await r.text()).slice(0, 200));
          sent = r.status < 500;
          if (!sent) throw new Error(`服务端 ${r.status}`);
        } catch (e) {
          if (String(e.message).includes("401")) throw e;
          log(`  回传失败(第 ${k + 1} 次):`, e.message);
          if (k < 5) await sleep(3000 * 2 ** k);
        }
      }
      if (!sent) log("  回传多次失败,放弃这条;服务端会在超时后把任务退回待领取,稍后重做");
    }
    await sleep(tasks.length ? 3000 : 10000);
  }
}
