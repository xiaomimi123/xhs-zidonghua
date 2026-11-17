// 多选下拉(笔记库筛选用,员工端和管理端共用)。
// 同一个筛选项里选了几个就是「或」,不同筛选项之间是「且」;选项旁边的数字是按其他已选条件统计的篇数。
// 用法:MS.html(key, 全部时的文字, 名词, 选项列表[{k,n}], 已选数组, 显示名函数) 放进页面,再 MS.bind(根元素, (key, 新的已选数组) => …)。
// 值传给接口时用 MS.join 连成一个字符串(接口按同一个分隔符拆开)。
const MS = {
  SEP: "\x1f",
  open: null,   // 当前展开的是哪一项:选一项后页面会重画,靠它把面板重新展开,方便连着勾几个
  join: a => (Array.isArray(a) ? a : a ? [a] : []).join("\x1f"),
  label(label, noun, cur, name) {
    const e = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    return !cur.length ? e(label) : cur.length === 1 ? e(name(cur[0])) : `${e(noun)} · ${cur.length} 项`;
  },
  html(key, label, noun, list, cur, name = k => k) {
    const e = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const on = cur.length > 0, open = MS.open === key;
    return `<div class="ms" data-ms="${e(key)}">
      <button type="button" class="ms-b${on ? " on" : ""}" aria-haspopup="true" aria-expanded="${open}" aria-label="${e(noun)}筛选" title="${cur.length ? cur.map(v => e(name(v))).join("、") : e(label)}"><span class="ms-t">${MS.label(label, noun, cur, name)}</span><i aria-hidden="true"></i></button>
      <div class="ms-p" role="group" aria-label="${e(noun)}"${open ? "" : " hidden"}>
        ${list.length ? list.map(x => `<label class="ms-o${x.n ? "" : " zero"}"><input type="checkbox" value="${e(x.k)}"${cur.includes(x.k) ? " checked" : ""}><span>${e(name(x.k))}</span><em>${x.n}</em></label>`).join("") : `<div class="ms-empty">没有可选项</div>`}
        ${on ? `<button type="button" class="ms-clear">清除${e(noun)}筛选</button>` : ""}
      </div></div>`;
  },
  bind(root, onChange) {
    root.querySelectorAll(".ms").forEach(box => {
      const key = box.dataset.ms, btn = box.querySelector(".ms-b"), panel = box.querySelector(".ms-p");
      btn.onclick = e => { e.stopPropagation(); const show = panel.hidden; MS.closeAll(root); panel.hidden = !show; btn.setAttribute("aria-expanded", show); MS.open = show ? key : null; };
      panel.onclick = e => e.stopPropagation();
      panel.onchange = () => { MS.open = key; onChange(key, [...panel.querySelectorAll("input:checked")].map(i => i.value)); };
      const clr = panel.querySelector(".ms-clear");
      if (clr) clr.onclick = () => { MS.open = key; onChange(key, []); };
    });
  },
  closeAll(root = document) { root.querySelectorAll(".ms-p").forEach(p => { p.hidden = true; }); root.querySelectorAll(".ms-b").forEach(b => b.setAttribute("aria-expanded", "false")); },
};
document.addEventListener("click", () => { if (MS.open) { MS.open = null; MS.closeAll(); } });
document.addEventListener("keydown", e => { if (e.key === "Escape" && MS.open) { MS.open = null; MS.closeAll(); } });
