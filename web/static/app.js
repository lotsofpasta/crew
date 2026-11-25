// Shared by every page: sign-in, API calls, polling, small helpers. No framework, no build step.
// Pages use: Crew.api(path, {method, body}), Crew.requireLogin(), Crew.poll(fn, ms), Crew.esc(s), Crew.ago(t)
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
  function ago(t) {
    if (!t) return "never";
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return Math.round(s) + "s ago";
    if (s < 3600) return Math.round(s / 60) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  }
  const time = (t) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  window.Crew = { api, login, logout, requireLogin, poll, esc, ago, time, token: store };
})();
