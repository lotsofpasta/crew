"""The hub: dashboard API, the agents' sync endpoint, and the static pages. Run: python -m crew serve

Access
  owner   logged in with the dashboard password (POST /api/login) - full control
  viewer  VIEWER_PASSWORD_HASH login or VIEWER_KEY - read-only
  local   a direct request from this machine without a token gets LOCAL_ROLE (default viewer)
  agent   each agent's own token (AGENT_TOKEN_<KEY>) opens its sync endpoint and nothing else
"""
import hmac
import logging
import os
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from . import auth as authmod, config, db, notify
from .agents import drops, registry, signals, store, sync, tickets

log = logging.getLogger("crew.server")
WEB = config.ROOT / "web"
app = FastAPI(title=config.BRAND_NAME, docs_url=None, redoc_url=None, openapi_url=None)
conn = db.ThreadLocalConn()

ALLOWED_HOSTS = {"localhost", "127.0.0.1", "testserver"} | {
    h.strip().lower() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()}
if config.PUBLIC_URL:
    from urllib.parse import urlparse
    ALLOWED_HOSTS.add((urlparse(config.PUBLIC_URL).hostname or "").lower())
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    # pages are self-contained; add a CDN host to script-src if your visualizer needs a library (see DESIGN.md)
    "Content-Security-Policy": os.environ.get("CONTENT_SECURITY_POLICY") or (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; connect-src 'self'; font-src 'self' data:; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'")}


@app.middleware("http")
async def guard(request: Request, call_next):
    # DNS-rebinding guard: only our own host names are served
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
    if host not in ALLOWED_HOSTS:
        return JSONResponse({"detail": f"unknown host {host!r} - add it to ALLOWED_HOSTS"}, status_code=421)
    resp = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        resp.headers.setdefault(k, v)
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return resp


def _init():
    store.init(conn)
    tickets.init(conn)
    drops.init(conn)
    authmod.init(conn)


_init()


# ------------------------------------------------------------------------------------------------ access
def client_ip(request):
    if os.environ.get("TRUST_PROXY_HEADERS") == "1":
        fwd = request.headers.get("cf-connecting-ip") or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if fwd:
            return fwd
    return request.client.host if request.client else "?"


def bearer(authorization):
    return authorization.removeprefix("Bearer ").strip()


def role(request: Request, authorization: str = Header(default="")):
    token = bearer(authorization)
    if token:
        r = authmod.session_role(conn, token)
        if r:
            return r
        vk = authmod.viewer_key()
        if vk and hmac.compare_digest(token, vk):
            return "viewer"
        raise HTTPException(401, "login required")
    proxied = any(h in request.headers for h in ("cf-connecting-ip", "x-forwarded-for", "forwarded", "x-real-ip"))
    local = request.client is not None and request.client.host in ("127.0.0.1", "::1") and not proxied
    if local:
        return "owner" if os.environ.get("LOCAL_ROLE", "viewer") == "owner" else "viewer"
    raise HTTPException(401, "login required")


def auth(r: str = Depends(role)):
    return r


def owner(r: str = Depends(role)):
    if r != "owner":
        raise HTTPException(403, "view-only access")
    return r


# ------------------------------------------------------------------------------------------------ pages
PAGES = {"": "index.html", "exchange": "exchange.html", "visualizer": "visualizer.html"}


@app.get("/", include_in_schema=False)
@app.get("/exchange", include_in_schema=False)
@app.get("/visualizer", include_in_schema=False)
def page(request: Request):
    # page code only: every number on it comes from the protected API
    return FileResponse(WEB / PAGES[request.url.path.strip("/")], headers={"Cache-Control": "no-cache"})


STATIC_TYPES = {".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".png": "image/png",
                ".jpg": "image/jpeg", ".webp": "image/webp", ".woff2": "font/woff2", ".json": "application/json",
                ".ico": "image/x-icon"}


@app.get("/static/{path:path}", include_in_schema=False)
def static(path: str):
    """Everything under web/static/ (styles, scripts, your art). Public: it holds no data."""
    base = (WEB / "static").resolve()
    p = (base / path).resolve()
    if base not in p.parents or not p.is_file() or p.suffix not in STATIC_TYPES:
        raise HTTPException(404)
    return FileResponse(p, media_type=STATIC_TYPES[p.suffix], headers={"Cache-Control": "no-cache"})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    p = WEB / "static" / "favicon.svg"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/svg+xml")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/brand")
def brand():
    """Public: what the login screen needs before anyone signs in."""
    return {"name": config.BRAND_NAME, "tagline": config.BRAND_TAGLINE}


# ------------------------------------------------------------------------------------------------ login
@app.get("/api/whoami")
def whoami(r: str = Depends(role)):
    return {"role": r, "brand": config.BRAND_NAME, "tagline": config.BRAND_TAGLINE}


@app.post("/api/login")
def login(request: Request, body: dict):
    ip, ua = client_ip(request), request.headers.get("user-agent", "")
    blocked = authmod.login_blocked(ip)
    if blocked:
        raise HTTPException(429, blocked)
    pw = str(body.get("password") or "")
    if authmod.owner_password_ok(pw):
        new_net, new_dev = authmod.owner_login_seen(conn, ip, ua)
        if new_net or new_dev:
            what = "new device and network" if new_net and new_dev else "new device" if new_dev else "new network"
            notify.send_async(f"{config.BRAND_NAME}: owner sign-in from a {what}\nFrom {ip}\n{ua[:80]}\n"
                              "Not you? Log out everywhere and change the password.")
        db.event(conn, "info", "security", "Owner signed in", f"{ip} · {ua[:80]}")
        return {"token": authmod.new_session(conn, "owner", ip, ua), "role": "owner"}
    if authmod.viewer_password_ok(pw):
        return {"token": authmod.new_session(conn, "viewer", ip, ua), "role": "viewer"}
    vk = authmod.viewer_key()
    if pw and vk and hmac.compare_digest(pw, vk):
        return {"token": vk, "role": "viewer"}
    if authmod.login_failed(ip) and time.time() - authmod._alerted["t"] > authmod.FAIL_WINDOW_S:
        authmod._alerted["t"] = time.time()
        notify.send_async(f"{config.BRAND_NAME}: {authmod.GLOBAL_FAILS} wrong passwords in 15 minutes (latest from {ip}). "
                          "Logins are paused for 15 minutes.")
    if not authmod.owner_login_ready():
        raise HTTPException(401, "no owner password set yet - run: python -m crew setpass")
    raise HTTPException(401, "wrong password")


@app.post("/api/logout")
def logout(body: dict, authorization: str = Header(default=""), r: str = Depends(role)):
    if body.get("everywhere"):
        if r != "owner":
            raise HTTPException(403, "view-only access")
        n = authmod.end_session(conn, "", everywhere=True)
        db.event(conn, "warn", "security", "Logged out everywhere", f"{n} session(s) ended")
        return {"ended": n}
    return {"ended": authmod.end_session(conn, bearer(authorization))}


# ------------------------------------------------------------------------------------------------ activity log
@app.get("/api/events", dependencies=[Depends(auth)])
def events(after: int = 0, limit: int = 100):
    return db.rows(conn, "SELECT * FROM events WHERE id>? ORDER BY id DESC LIMIT ?", (after, max(1, min(limit, 1000))))


# ------------------------------------------------------------------------------------------------ agents
@app.get("/api/agents", dependencies=[Depends(auth)])
def agents_get():
    """The team (roster with live status and counts), what waits for the owner, and recent reports."""
    return {"agents": registry.roster(conn), "lines": registry.lines(), "manager": registry.manager(),
            "approvals": store.approvals(conn), "reports": store.recent(conn), "tickets": tickets.recent(conn)}


@app.get("/api/agents/feed", dependencies=[Depends(auth)])
def agents_feed(agent: str = "", kind: str = "", before: int = 0, limit: int = 40, unread: bool = False):
    if kind and kind not in store.KINDS:
        raise HTTPException(400, f"kind must be one of {store.KINDS}")
    return store.feed(conn, agent or None, kind or None, before or None, limit, unread)


@app.post("/api/agents/seen_all", dependencies=[Depends(owner)])
def agents_seen_all(body: dict):
    q, p = "UPDATE agent_reports SET seen_t=? WHERE seen_t IS NULL", [db.now_ms()]
    if body.get("agent"):
        q += " AND agent=?"; p.append(str(body["agent"]))
    if body.get("kind"):
        q += " AND kind=?"; p.append(str(body["kind"]))
    n = conn.execute(q, p).rowcount
    conn.commit()
    return {"seen": n}


def _report(rid):
    rep = store.get(conn, rid)
    if not rep:
        raise HTTPException(404, "no such report")
    return rep


@app.post("/api/agents/reports/{rid}/seen", dependencies=[Depends(owner)])
def report_seen(rid: int, body: dict):
    _report(rid)
    conn.execute("UPDATE agent_reports SET seen_t=? WHERE id=?", (db.now_ms() if body.get("seen", True) else None, rid))
    conn.commit()
    return {"ok": True}


@app.post("/api/agents/reports/{rid}/approve", dependencies=[Depends(owner)])
def report_approve(rid: int):
    """The owner approves the action the agent proposed; the agent gets it on its next sync and reports back."""
    rep = _report(rid)
    if rep["status"] != "open" or rep["action_state"] != "proposed":
        raise HTTPException(409, "nothing to approve on this report")
    label = (rep["action"] or {}).get("label", "action")
    store.set_action(conn, rid, "approved", "approved - the agent runs it on its next sync")
    signals.emit(conn, "you", rep["agent"], "approve", f"Approved: {label}", rep["title"], ref=f"report:{rid}", chat=True)
    db.event(conn, "info", "approval", f"Approved: {label}", f"{rep['agent']} · {rep['title']}")
    return {"ok": True}


@app.post("/api/agents/reports/{rid}/reject", dependencies=[Depends(owner)])
def report_reject(rid: int):
    rep = _report(rid)
    if rep["status"] != "open" or rep["action_state"] != "proposed":
        raise HTTPException(409, "nothing to reject on this report")
    label = (rep["action"] or {}).get("label", "proposal")
    store.set_action(conn, rid, "rejected", "rejected by the owner")
    signals.emit(conn, "you", rep["agent"], "reject", f"Rejected: {label}", rep["title"], ref=f"report:{rid}", chat=True)
    store.resolve(conn, rid, "Rejected by the owner.")
    db.event(conn, "info", "approval", f"Rejected: {label}", f"{rep['agent']} · {rep['title']}")
    return {"ok": True}


@app.post("/api/agents/reports/{rid}/dismiss", dependencies=[Depends(owner)])
def report_dismiss(rid: int):
    """Close an open issue by hand (the agent opens it again if the condition comes back later)."""
    rep = _report(rid)
    if rep["status"] != "open":
        raise HTTPException(409, "already closed")
    store.resolve(conn, rid, "Dismissed by the owner.")
    return {"ok": True}


@app.post("/api/agents/{key}/sync")
def agent_sync(key: str, body: dict, authorization: str = Header(default="")):
    """The agents' only door (crew/agents/sync.py). Each agent has its own token; it opens this endpoint and nothing else."""
    if not registry.get(key):
        raise HTTPException(404, "no such agent in agents.yaml")
    want, got = registry.token(key), bearer(authorization)
    if not want or not got or not hmac.compare_digest(got, want):
        raise HTTPException(401, "agent token required")
    before = signals.last_id(conn)
    res = sync.handle(conn, key, body)
    _forward_alerts(before)
    _release_drops()
    return res


def _forward_alerts(after_id):
    """Telegram for the report levels the owner asked for (TELEGRAM_LEVELS, default crit + approval requests)."""
    if not notify.enabled():
        return
    want = notify.levels()
    for s in signals.since(conn, after_id, 50, chat=True):
        if s["dst"] == "you" and (s["kind"] in want or (s.get("level") in want and s["kind"] in ("issue", "finding"))):
            a = registry.get(s["src"]) or {"name": s["src"]}
            notify.send_async(f"{config.BRAND_NAME} · {a['name']}{' asks for approval' if s['kind'] == 'approval' else ''}\n"
                              f"{s['label']}\n{(s.get('text') or '')[:600]}\n{config.PUBLIC_URL}/exchange")


def _release_drops(did=None):
    return drops.release(conn, registry.manager(), set(registry.keys()), did=did)


# ------------------------------------------------------------------------------------------------ exchange
@app.get("/api/exchange", dependencies=[Depends(auth)])
def exchange(after: int = 0, limit: int = 300):
    """The board: the team, the lines, waiting approvals and the signals after `after` (newest ones on the first call)."""
    _release_drops()
    limit = max(1, min(limit, 1000))
    sig = signals.since(conn, after, limit) if after else signals.recent(conn, limit)
    return {"t": db.now_ms(), "brand": config.BRAND_NAME, "lines": registry.lines(), "agents": registry.roster(conn),
            "approvals": [r["id"] for r in store.approvals(conn)], "signals": sig}


@app.get("/api/exchange/chat", dependencies=[Depends(auth)])
def exchange_chat(before: int = 0, limit: int = 60, src: str = ""):
    return signals.recent(conn, max(1, min(limit, 200)), chat=True, before=before or None, src=src[:32] or None)


@app.get("/api/exchange/replay", dependencies=[Depends(auth)])
def exchange_replay(hours: int = 24):
    now = db.now_ms()
    t0 = now - max(1, min(hours, 24 * signals.KEEP_DAYS)) * 3_600_000
    return {"t0": t0, "t1": now, "signals": signals.window(conn, t0, now + 1)}


@app.post("/api/exchange/say", dependencies=[Depends(owner)])
def exchange_say(body: dict):
    """The owner sends a chat message straight to one agent (it reads it on its next sync)."""
    to, text = body.get("to"), str(body.get("text") or "").strip()
    if to not in registry.keys():
        raise HTTPException(400, "pick an agent")
    if not text or len(text) > 4000:
        raise HTTPException(400, "write something (under 4000 characters)")
    sid = signals.emit(conn, "you", to, "message", text if len(text) <= 300 else text[:297] + "…",
                       text if len(text) > 300 else None, chat=True)
    return {"id": sid}


@app.post("/api/exchange/drop", dependencies=[Depends(owner)])
def exchange_drop(body: dict):
    """A link or note for the team: queued, then released to the manager (or @agent) at a steady pace."""
    try:
        d = drops.add(conn, body.get("text"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    _release_drops()
    return {"drop": d["id"], **drops.overview(conn)}


@app.get("/api/exchange/queue", dependencies=[Depends(auth)])
def exchange_queue():
    return drops.overview(conn)


@app.post("/api/exchange/queue/{did}", dependencies=[Depends(owner)])
def exchange_queue_act(did: int, body: dict):
    """{"act": "now" | "top" | "remove"} on a queued drop."""
    act = body.get("act")
    ok = (_release_drops(did) is not None if act == "now" else drops.bump(conn, did) if act == "top"
          else drops.remove(conn, did) if act == "remove" else None)
    if ok is None:
        raise HTTPException(400, "act must be now, top or remove")
    if not ok:
        raise HTTPException(404, "not in the queue any more")
    return drops.overview(conn)


@app.get("/api/tickets", dependencies=[Depends(auth)])
def tickets_get(days: int = 7):
    return tickets.recent(conn, max(1, min(days, 90)))


@app.post("/api/tickets", dependencies=[Depends(owner)])
def tickets_post(body: dict):
    """The owner puts a task on an agent's desk."""
    to = body.get("to") or registry.manager()
    if to not in registry.keys():
        raise HTTPException(400, "pick an agent")
    t, new = tickets.file(conn, "you", to, str(body.get("title") or "") or tickets.title_of(body.get("body")), str(body.get("body") or ""))
    if not t:
        raise HTTPException(400, "a title is needed")
    return {"ticket": t, "new": new}


# ------------------------------------------------------------------------------------------------ visualizer contract
@app.get("/api/viz", dependencies=[Depends(auth)])
def viz(after: int = 0, limit: int = 300):
    """One stable, render-ready snapshot for visualizers (see DESIGN.md). Poll with ?after=<cursor>.

    nodes  every agent and line: {id, type: agent|line, label, role, parent, alive, busy, doing, heartbeat, attention, ...extra}
    edges  the reporting tree: {from, to, type: "reports_to"}
    pulses signals after the cursor: {id, t, from, to, kind, label, chat, level, ref}
    """
    now = db.now_ms()
    nodes = []
    for a in registry.roster(conn, now):
        c = a.get("counts") or {}
        extra = {k: v for k, v in a.items() if k not in ("key", "name", "role", "reports_to", "about", "does", "uses", "limits",
                                                         "runs_on", "model", "light_model", "counts", "heartbeat", "alive", "doing", "slot")}
        nodes.append({"id": a["key"], "type": "agent", "label": a["name"], "role": a.get("role"), "parent": a.get("reports_to"),
                      "alive": a["alive"], "doing": a.get("doing"), "heartbeat": a.get("heartbeat"), "slot": a["slot"],
                      "attention": (c.get("approvals", 0) and "approval") or (c.get("crit", 0) and "crit")
                      or (c.get("issues", 0) and "issue") or None, **extra})
    for ln in registry.lines():
        nodes.append({"id": ln["key"], "type": "line", "label": ln.get("label", ln["key"]), "alive": True,
                      **{k: v for k, v in ln.items() if k not in ("key", "label")}})
    edges = [{"from": a["key"], "to": a.get("reports_to"), "type": "reports_to"} for a in registry.team() if a.get("reports_to")]
    sig = signals.since(conn, after, max(1, min(limit, 1000))) if after else signals.recent(conn, max(1, min(limit, 1000)))
    pulses = [{"id": s["id"], "t": s["t"], "from": s["src"], "to": s["dst"], "kind": s["kind"], "label": s["label"],
               "chat": s["chat"], "level": s.get("level"), "ref": s.get("ref"), "meta": s.get("meta")} for s in sig]
    busy_since = now - 60_000
    active = {p["from"] for p in pulses if p["t"] >= busy_since} | {p["to"] for p in pulses if p["t"] >= busy_since}
    for n in nodes:
        n["busy"] = n["id"] in active
    return {"t": now, "brand": config.BRAND_NAME, "cursor": pulses[-1]["id"] if pulses else after,
            "nodes": nodes, "edges": edges, "pulses": pulses, "approvals": len(store.approvals(conn))}


def main():
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if not authmod.owner_login_ready():
        log.warning("No owner password yet: run `python -m crew setpass` (the dashboard is view-only until then)")
    uvicorn.run(app, host=config.env("HOST", "127.0.0.1"), port=int(config.env("PORT", "8000")), log_level="warning",
                proxy_headers=False)


if __name__ == "__main__":
    main()
