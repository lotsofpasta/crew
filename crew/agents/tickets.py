"""Work requests between agents: anyone (an agent or the owner, 'you') files a ticket to an agent; that agent accepts
or declines it, then finishes it with a deliverable that goes back to whoever asked.

status  open -> accepted -> done, or open/accepted -> declined
Every step is a chat message on the exchange (requester -> worker when filed, worker -> requester after).
A requester may file at most PER_DAY tickets a day (an agent stuck in a loop cannot flood a desk).
"""
import json
import re

from .. import clock, db
from . import signals

SCHEMA = """CREATE TABLE IF NOT EXISTS agent_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, t INTEGER NOT NULL, src TEXT NOT NULL, dst TEXT NOT NULL,
    title TEXT NOT NULL, body TEXT, status TEXT NOT NULL DEFAULT 'open', priority TEXT, updated_t INTEGER,
    note TEXT, result_title TEXT, result_body TEXT, meta TEXT);
CREATE INDEX IF NOT EXISTS agent_tickets_status ON agent_tickets(dst, status);"""
OPEN = ("open", "accepted")
PER_DAY = 12


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def _row(r):
    d = dict(r)
    d["meta"] = json.loads(d["meta"]) if d.get("meta") else None
    return d


def _norm(s):
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", str(s).lower()).split())[:120]


def title_of(text, n=110):
    """A short title from a longer request: its first sentence, cut at a word boundary."""
    s = re.split(r"(?<=[.?!])\s", " ".join(str(text or "").split()), maxsplit=1)[0]
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def get(conn, tid):
    init(conn)
    r = conn.execute("SELECT * FROM agent_tickets WHERE id=?", (tid,)).fetchone()
    return _row(r) if r else None


def file(conn, src, dst, title, body="", now=None, meta=None):
    """-> (ticket, new). The same open request from the same requester is not filed twice; past the daily cap
    nothing is filed (ticket None). The owner ('you') has no cap."""
    init(conn)
    now = now or db.now_ms()
    title, body = str(title or "").strip()[:200], str(body or "").strip()[:4000]
    if not title or not dst or dst == src:
        return None, False
    for r in conn.execute("SELECT * FROM agent_tickets WHERE src=? AND dst=? AND status IN ('open','accepted')", (src, dst)):
        if _norm(r["title"]) == _norm(title):
            return _row(r), False
    n = conn.execute("SELECT COUNT(*) FROM agent_tickets WHERE src=? AND t>=?", (src, now - clock.DAY_MS)).fetchone()[0]
    if n >= PER_DAY and src != "you":
        return None, False
    tid = conn.execute("INSERT INTO agent_tickets(t, src, dst, title, body, status, updated_t, meta) VALUES (?,?,?,?,?,'open',?,?)",
                       (now, src, dst, title, body, now, json.dumps(meta) if meta else None)).lastrowid
    conn.commit()
    signals.emit(conn, src, dst, "ticket", title, body or None, ref=f"ticket:{tid}", chat=True, t=now)
    return get(conn, tid), True


def update(conn, tid, status, note=None, priority=None, now=None, by=None):
    """The worker accepts or declines. `note`: its words to the requester (the chat line). `by`: who is updating
    (only the ticket's worker may)."""
    cur = get(conn, tid)
    if not cur or cur["status"] in ("done", "declined") or status not in ("accepted", "declined"):
        return False
    if by is not None and by != cur["dst"]:
        return False
    if cur["status"] == status and (priority or cur["priority"]) == cur["priority"]:
        return False
    now = now or db.now_ms()
    conn.execute("UPDATE agent_tickets SET status=?, note=COALESCE(?, note), priority=COALESCE(?, priority), updated_t=? WHERE id=?",
                 (status, note and note[:1000], priority, now, tid))
    conn.commit()
    signals.emit(conn, cur["dst"], cur["src"], "accept" if status == "accepted" else "decline",
                 (note or ("On it." if status == "accepted" else "Not taking this one."))[:300], None,
                 ref=f"ticket:{tid}", chat=True, t=now)
    return True


def finish(conn, tid, title, body, now=None, by=None):
    """The deliverable goes back to whoever asked (Agents feed + exchange chat)."""
    from . import store
    cur = get(conn, tid)
    if not cur or cur["status"] in ("done", "declined"):
        return False
    if by is not None and by != cur["dst"]:
        return False
    now = now or db.now_ms()
    conn.execute("UPDATE agent_tickets SET status='done', result_title=?, result_body=?, updated_t=? WHERE id=?",
                 (str(title)[:200], str(body)[:8000], now, tid))
    conn.commit()
    store.deliver(conn, cur["dst"], f"ticket:{tid}", str(title)[:140], str(body)[:3000],
                  {"ticket": tid, "asked_by": cur["src"], "request": cur["title"]}, ref=f"ticket:{tid}", now=now, to=cur["src"])
    return True


def open_for(conn, dst):
    init(conn)
    return [_row(r) for r in conn.execute("SELECT * FROM agent_tickets WHERE dst=? AND status IN ('open','accepted') ORDER BY id", (dst,))]


def answered(conn, src, since_t):
    """Tickets `src` filed that were finished or declined after since_t."""
    init(conn)
    return [_row(r) for r in conn.execute("SELECT * FROM agent_tickets WHERE src=? AND status IN ('done','declined') AND updated_t>? "
                                          "ORDER BY updated_t", (src, since_t))]


def recent(conn, days=7, now=None):
    init(conn)
    now = now or db.now_ms()
    return [_row(r) for r in conn.execute("SELECT * FROM agent_tickets WHERE t>=? OR status IN ('open','accepted') ORDER BY id DESC",
                                          (now - days * clock.DAY_MS,))]
