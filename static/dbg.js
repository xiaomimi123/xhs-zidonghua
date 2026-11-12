// 输入调试面板:只在网址带 ?dbg=1 时启用。把输入框的聚焦 / 输入法组合 / 按键 / 输入事件,以及输入框被重绘的时刻记在右下角,
// 方便在某台电脑上输入异常时截图给开发看。平时不加载任何东西。
(() => {
  if (!/[?&]dbg=1/.test(location.search)) return;
  const box = document.createElement("div");
  box.style.cssText = "position:fixed;right:8px;bottom:8px;width:440px;max-width:94vw;height:300px;z-index:99999;background:#111;color:#cfe;font:11px/1.45 Consolas,monospace;border-radius:8px;display:flex;flex-direction:column;box-shadow:0 4px 20px #0006";
  box.innerHTML = '<div style="padding:5px 8px;background:#222;border-radius:8px 8px 0 0;display:flex;justify-content:space-between"><b>输入调试 (?dbg=1)</b><span><a href="#" id="dbgc" style="color:#8cf">清空</a></span></div><pre id="dbgp" style="margin:0;padding:6px 8px;overflow:auto;flex:1;white-space:pre-wrap;word-break:break-all"></pre>';
  document.body.appendChild(box);
  const pre = box.querySelector("#dbgp");
  box.querySelector("#dbgc").onclick = e => { e.preventDefault(); pre.textContent = ""; };
  const log = (...a) => { pre.textContent += `${(performance.now() / 1000).toFixed(2)}  ${a.join(" ")}\n`; pre.scrollTop = pre.scrollHeight; };
  const isField = t => t && t.matches && t.matches("input, textarea");
  ["focusin", "focusout", "compositionstart", "compositionupdate", "compositionend", "beforeinput", "input", "keydown", "keyup"].forEach(type =>
    document.addEventListener(type, e => {
      if (!isField(e.target) || box.contains(e.target)) return;
      log(type, `[${e.target.id || e.target.name || e.target.placeholder || e.target.type}]`, e.key !== undefined ? `key=${e.key}` : "", e.isComposing ? "composing" : "", e.data != null ? `data=${e.data}` : "", `value=${JSON.stringify(e.target.value)}`, e.defaultPrevented ? "PREVENTED" : "");
    }, true));
  new MutationObserver(ms => {
    for (const m of ms) for (const n of m.removedNodes) {
      if (n.nodeType !== 1 || box.contains(n) || n === box) continue;
      if (n.matches && (n.matches("input, textarea") || n.querySelector("input, textarea"))) log("DOM:", "含输入框的区域被移除(重绘)", n.id || n.className || n.tagName);
    }
  }).observe(document.body, { childList: true, subtree: true });
  window.addEventListener("error", e => log("ERROR:", e.message));
  window.addEventListener("unhandledrejection", e => log("PROMISE:", e.reason && e.reason.message || e.reason));
  log("已启用。点搜索框打几个字,把这个面板截图发给我。UA:", navigator.userAgent.slice(0, 90));
})();
