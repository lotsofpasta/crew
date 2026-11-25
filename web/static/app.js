// Shared by every page: sign-in, API calls, polling, small helpers. No framework, no build step.
// Pages use: Crew.api(path, {method, body}), Crew.requireLogin(), Crew.poll(fn, ms), Crew.esc(s), Crew.md(text) / Crew.mdInline(line) (safe markdown -> HTML), Crew.ago(t)
(function () {
  const TOKEN_KEY = "crew_token";
  const store = {
    get() { try { return localStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; } },
    set(v) { try { v ? localStorage.setItem(TOKEN_KEY, v) : localStorage.removeItem(TOKEN_KEY); } catch (e) {} },
  };
  // a view-only link: /exchange?key=<VIEWER_KEY>
  const qk = new URLSearchParams(location.search).get("key");
  if (qk) { store.set(qk); history.replaceState(null, "", location.pathname); }

  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json" };
    const tok = store.get();
    if (tok) headers.Authorization = "Bearer " + tok;
    const res = await fetch(path, { method: opts.method || "GET", headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
    if (res.status === 401 && !opts.noRedirect) {
      store.set("");
      if (location.pathname !== "/") location.href = "/?next=" + encodeURIComponent(location.pathname);
      throw new Error("login required");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    return data;
  }

  async function login(password) {
    const r = await api("/api/login", { method: "POST", body: { password }, noRedirect: true });
    store.set(r.token);
    return r;
  }

  async function logout(everywhere) {
    try { await api("/api/logout", { method: "POST", body: { everywhere: !!everywhere }, noRedirect: true }); } catch (e) {}
    store.set("");
    location.href = "/";
  }

  // -> {role, brand} or redirects to the login screen
  async function requireLogin() { return api("/api/whoami"); }

  // run fn now and every ms; pauses while the tab is hidden
  function poll(fn, ms) {
    let timer = null;
    const tick = async () => { try { await fn(); } catch (e) { console.warn(e); } timer = setTimeout(tick, ms); };
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) { clearTimeout(timer); timer = null; } else if (!timer) tick();
    });
    tick();
  }

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Markdown for agent text: escapes everything first, then adds a small, safe subset - paragraphs, # headings,
  // - / 1. lists, > quotes, ``` code blocks, `code`, **bold**, *italic*, [links](https://...) and bare URLs.
  // No raw HTML gets through, and only http(s) links are made clickable.
  function inline(s) {
    const codes = [];
    s = s.replace(/`([^`\n]+)`/g, (_, c) => `\u0000${codes.push(c) - 1}\u0000`);       // keep code spans untouched
    s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, t, u) => `<a href="${u}" target="_blank" rel="noopener noreferrer">${t}</a>`)
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:!?'"])/g, (_, p, u) => `${p}<a href="${u}" target="_blank" rel="noopener noreferrer">${u}</a>`)
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>").replace(/__([^_\n]+)__/g, "<strong>$1</strong>")
      .replace(/(^|[^*\w])\*([^*\s][^*\n]*?)\*(?!\w)/g, "$1<em>$2</em>").replace(/(^|[^_\w])_([^_\s][^_\n]*?)_(?!\w)/g, "$1<em>$2</em>");
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`);
  }
  function md(text) {
    const lines = esc(text).replace(/\r\n?/g, "\n").split("\n"), out = [];
    let i = 0;
    while (i < lines.length) {
      const l = lines[i];
      if (/^\s*```/.test(l)) {                                                        // fenced code
        const code = [];
        for (i++; i < lines.length && !/^\s*```/.test(lines[i]); i++) code.push(lines[i]);
        out.push(`<pre><code>${code.join("\n")}</code></pre>`); i++; continue;
      }
      let m;
      if ((m = l.match(/^\s*(#{1,6})\s+(.*)$/))) { out.push(`<p class="md-h">${inline(m[2])}</p>`); i++; continue; }
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(l)) { out.push("<hr>"); i++; continue; }
      if (/^\s*&gt;\s?/.test(l)) {
        const q = [];
        for (; i < lines.length && /^\s*&gt;\s?/.test(lines[i]); i++) q.push(lines[i].replace(/^\s*&gt;\s?/, ""));
        out.push(`<blockquote>${md_unesc(q)}</blockquote>`); continue;
      }
      if ((m = l.match(/^\s*([-*+]|\d+[.)])\s+/))) {                                 // a list: one kind per block
        const ordered = /\d/.test(m[1]), items = [];
        const re = ordered ? /^\s*\d+[.)]\s+(.*)$/ : /^\s*[-*+]\s+(.*)$/;
        for (; i < lines.length; i++) {
          const x = lines[i].match(re);
          if (x) items.push(inline(x[1]));
          else if (items.length && /^\s{2,}\S/.test(lines[i])) items[items.length - 1] += " " + inline(lines[i].trim());
          else break;
        }
        out.push(`<${ordered ? "ol" : "ul"}>${items.map((x) => `<li>${x}</li>`).join("")}</${ordered ? "ol" : "ul"}>`); continue;
      }
      if (!l.trim()) { i++; continue; }
      const para = [];                                                               // a paragraph runs to a blank line or a block
      for (; i < lines.length && lines[i].trim() && !/^\s*(```|#{1,6}\s|&gt;|[-*+]\s|\d+[.)]\s)/.test(lines[i]); i++) para.push(inline(lines[i]));
      if (!para.length) { out.push(`<p>${inline(l)}</p>`); i++; continue; }
      out.push(`<p>${para.join("<br>")}</p>`);
    }
    return `<div class="md">${out.join("")}</div>`;
  }
  // quotes hold already-escaped lines: render them without escaping twice
  const md_unesc = (q) => md(q.join("\n").replace(/&lt;|&gt;|&quot;|&#39;|&amp;/g, (e) => ({ "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&amp;": "&" }[e])));
  function ago(t) {
    if (!t) return "never";
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return Math.round(s) + "s ago";
    if (s < 3600) return Math.round(s / 60) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  }
  const time = (t) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  // An agent's colour: its own `colour:` from agents.yaml, else the fixed palette slot (1-8, agents.yaml order),
  // else neutral. Colours are never cycled: a 9th agent is not a 9th hue.
  function agentColor(a) {
    const own = a && (a.colour || a.color);
    if (own && /^#[0-9a-f]{3,8}$/i.test(own)) return own;
    return a && a.slot >= 1 && a.slot <= 8 ? `var(--agent-${a.slot})` : "var(--agent-other)";
  }
  const initials = (name) => String(name || "?").split(/[\s_-]+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  // <span class="avatar"> with initials (identity never by colour alone) and a status dot
  const avatar = (a) => `<span class="avatar" style="--c:${agentColor(a)}" title="${esc(a.name)}">${esc(initials(a.name))}` +
    `<span class="dot ${a.alive ? "on" : ""}" aria-label="${a.alive ? "online" : "offline"}"></span></span>`;

  // header brand: <a class="brand" data-brand-link> gets the mark + BRAND_NAME
  function brand(name) {
    document.querySelectorAll("[data-brand]").forEach((e) => (e.textContent = name));
    document.querySelectorAll("[data-brand-link]").forEach((e) => {
      if (!e.querySelector(".mark")) e.insertAdjacentHTML("afterbegin", `<img class="mark" src="/static/brand/mark.svg" alt="">`);
    });
  }

  window.Crew = { api, login, logout, requireLogin, poll, esc, md, mdInline: (s) => inline(esc(s)), ago, time, token: store, agentColor, initials, avatar, brand };
})();
