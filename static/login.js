// ---------- 登录页(左右分屏):员工端和管理端共用 ----------
// loginScreen({ admin, staffMode, lastUser, submit }) 返回挂好的 .gate 元素;
// submit({ username, password, remember }) 成功时 resolve,失败时 throw Error(提示文案)。
function loginScreen({ admin = false, staffMode = true, lastUser = "", submit }) {
  const e = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const withUser = !admin && staffMode;
  const g = document.createElement("div");
  g.className = "gate login" + (admin ? " admin" : "");
  g.innerHTML = `
  <section class="lg-brand" aria-hidden="true">
    <div class="lg-head"><span class="logo">书</span><b>小红书流水线${admin ? " · 管理后台" : ""}</b></div>
    <div class="lg-cards">
      <div class="lg-nc c1"><i></i><b>早八通勤妆 5 分钟出门,手残党也能学会</b><small><span>@小鹿</span><span class="hot">♥ 3.2w</span></small></div>
      <div class="lg-nc c2"><i></i><b>减脂期一周早餐不重样,附采购清单</b><small><span>@阿柚</span><span class="hot">♥ 1.8w</span></small></div>
      <div class="lg-nc c3"><i></i><b>租房改造前后对比</b><small><span>@木木</span><span>♥ 9821</span></small></div>
    </div>
    <div class="lg-pitch"><h1>从爆款到成稿,<br>一条流水线跑完</h1><p>选赛道、定篇数,采集、拆解、复刻、配图自动完成。</p></div>
    <ol class="lg-loop"><li><em>01</em><span>批量复刻</span></li><li><em>02</em><span>发帖测试</span></li><li><em>03</em><span>数据归集</span></li><li><em>04</em><span>爆款再复刻</span></li></ol>
    <div class="lg-foot">发布仍由你在小红书手动完成 · 数据只在公司服务器</div>
  </section>
  <section class="lg-side">
    <form class="lg-form" autocomplete="on">
      <h2>${admin ? "管理后台" : withUser ? "欢迎回来" : "请输入访问密码"}</h2>
      <p class="lg-lead">${admin ? "人员、接口配置、数据看板" : withUser ? "登录后只看得到你自己的任务和成品" : "向管理员要访问密码"}</p>
      ${withUser ? `<div class="lg-field"><label for="guser">账号</label><div class="lg-inp">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21c1-4 4-6 8-6s7 2 8 6"/></svg>
        <input id="guser" name="username" autocomplete="username" placeholder="管理员分配的账号" value="${e(lastUser)}" required></div></div>` : ""}
      <div class="lg-field"><label for="gpw">${admin ? "管理员密码" : "密码"}</label><div class="lg-inp">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>
        <input id="gpw" name="password" type="password" autocomplete="current-password" placeholder="输入密码" required>
        <button type="button" class="lg-eye" aria-label="显示密码" aria-pressed="false"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg></button></div></div>
      <div class="lg-row">${withUser ? `<label><input type="checkbox" id="grem" checked>记住账号</label>` : "<span></span>"}<span>忘记密码请联系${admin ? "部署负责人" : "管理员"}</span></div>
      <button class="btn lg-btn">登录</button>
      <p class="gerr" role="alert"></p>
      <div class="lg-ffoot"><span>小红书流水线工作台</span><a href="${admin ? "/" : "/admin"}">${admin ? "← 员工登录" : "管理员入口 →"}</a></div>
    </form>
  </section>`;
  document.body.appendChild(g);
  const pw = g.querySelector("#gpw"), user = g.querySelector("#guser"), err = g.querySelector(".gerr"), btn = g.querySelector(".lg-btn");
  (user && !user.value ? user : pw).focus();
  g.querySelector(".lg-eye").onclick = ev => {
    const show = pw.type === "password"; pw.type = show ? "text" : "password";
    ev.currentTarget.setAttribute("aria-pressed", String(show)); ev.currentTarget.setAttribute("aria-label", show ? "隐藏密码" : "显示密码");
  };
  g.querySelector("form").onsubmit = async ev => {
    ev.preventDefault(); err.textContent = ""; btn.disabled = true; btn.textContent = "登录中…";
    try {
      await submit({ username: user ? user.value.trim() : "", password: pw.value, remember: !!g.querySelector("#grem")?.checked });
      g.remove();
    } catch (x) { err.textContent = x.message || "登录失败"; pw.select(); }
    finally { btn.disabled = false; btn.textContent = "登录"; }
  };
  return g;
}
