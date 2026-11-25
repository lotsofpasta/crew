"""The owner's drop box: links and short notes for the team to look into, released one at a time so the agents (and
their LLM budget) are never flooded.

  queued    the owner posted it; it waits its turn (bumped ones go first, then oldest)
  released  let out to the team: a chat message you -> the manager (agents.yaml `manager:`), or straight to the agent
            named with @key at the start of the text
The manager decides what happens next (reply, file a ticket to a teammate, ...). Pacing: at least GAP_MS between
releases and at most RELEASE_PER_DAY a day; the owner can send one out now.
"""
import json
import re

from .. import clock, db
from . import signals

SCHEMA = """CREATE TABLE IF NOT EXISTS owner_drops (
    id INTEGER PRIMARY KEY AUTOINCREMENT, t INTEGER NOT NULL, text TEXT NOT NULL, urls TEXT, status TEXT NOT NULL DEFAULT 'queued',
    dst TEXT, pri INTEGER NOT NULL DEFAULT 0, released_t INTEGER, updated_t INTEGER);
CREATE INDEX IF NOT EXISTS owner_drops_status ON owner_drops(status);"""
PER_DAY = 30
QUEUE_MAX = 40
GAP_MS = 20 * 60_000
RELEASE_PER_DAY = 24
MAX_TEXT = 2000
URL_RE = re.compile(r"https?://[^\s<>\"']+")


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def _row(r):
    d = dict(r)
    d["urls"] = json.loads(d["urls"]) if d.get("urls") else []
    return d


def get(conn, did):
    init(conn)
    r = conn.execute("SELECT * FROM owner_drops WHERE id=?", (did,)).fetchone()
    return _row(r) if r else None


def urls_in(text):
    return list(dict.fromkeys(u.rstrip(".,;:!?)]}") for u in URL_RE.findall(text or "")))[:10]


def addressed(text, keys):
    """'@key ...' at the start -> that agent key (if it is on the team), else None."""
    m = re.match(r"\s*@(\w+)", text or "")
    return m.group(1).lower() if m and m.group(1).lower() in keys else None


def add(conn, text, now=None):
    """The owner's post -> a queued drop, or raises ValueError."""
    init(conn)
    now = now or db.now_ms()
    text = str(text or "").strip()
    if not text:
        raise ValueError("nothing to send")
    if len(text) > MAX_TEXT:
        raise ValueError(f"keep it under {MAX_TEXT} characters")
    if conn.execute("SELECT COUNT(*) FROM owner_drops WHERE t>=?", (now - clock.DAY_MS,)).fetchone()[0] >= PER_DAY:
        raise ValueError(f"{PER_DAY} drops a day is the limit")
    if len(queue(conn)) >= QUEUE_MAX:
        raise ValueError(f"the queue is full ({QUEUE_MAX} waiting) - remove a few or let it drain")
    did = conn.execute("INSERT INTO owner_drops(t, text, urls, status, updated_t) VALUES (?,?,?,'queued',?)",
                       (now, text, json.dumps(urls_in(text)), now)).lastrowid
    conn.commit()
    return get(conn, did)


def queue(conn):
    init(conn)
    return [_row(r) for r in conn.execute("SELECT * FROM owner_drops WHERE status='queued' ORDER BY pri DESC, id")]


def next_at(conn, now=None):
    """Earliest time (ms) the pacing allows the next release."""
    init(conn)
    now = now or db.now_ms()
    rel = [r[0] for r in conn.execute("SELECT released_t FROM owner_drops WHERE released_t>=? ORDER BY released_t",
                                      (now - clock.DAY_MS,))]
    at = (rel[-1] + GAP_MS) if rel else now
    if len(rel) >= RELEASE_PER_DAY:
        at = max(at, rel[-RELEASE_PER_DAY] + clock.DAY_MS)
    return max(at, now)


def release(conn, manager, keys, now=None, did=None):
    """Let the next drop (or `did`, when the owner says send it now) out to the team. -> the drop or None."""
    init(conn)
    now = now or db.now_ms()
    if did is None:
        q = queue(conn)
        if not q or next_at(conn, now) > now:
            return None
        d = q[0]
    else:
        d = get(conn, did)
        if not d or d["status"] != "queued":
            return None
    dst = addressed(d["text"], keys) or manager
    if not dst:
        return None
    n = conn.execute("UPDATE owner_drops SET status='released', dst=?, released_t=?, updated_t=? WHERE id=? AND status='queued'",
                     (dst, now, now, d["id"])).rowcount
    conn.commit()
    if not n:
        return None
    text = d["text"]
    signals.emit(conn, "you", dst, "drop", text if len(text) <= 300 else text[:297] + "…", text if len(text) > 300 else None,
                 ref=f"drop:{d['id']}", chat=True, t=now, meta={"urls": d["urls"]} if d["urls"] else None)
    return get(conn, d["id"])


def bump(conn, did):
    init(conn)
    top = conn.execute("SELECT COALESCE(MAX(pri), 0) FROM owner_drops WHERE status='queued'").fetchone()[0]
    n = conn.execute("UPDATE owner_drops SET pri=? WHERE id=? AND status='queued'", (top + 1, did)).rowcount
    conn.commit()
    return bool(n)


def remove(conn, did):
    init(conn)
    n = conn.execute("DELETE FROM owner_drops WHERE id=? AND status='queued'", (did,)).rowcount
    conn.commit()
    return bool(n)


def overview(conn, now=None):
    now = now or db.now_ms()
    q = queue(conn)
    return {"queue": [{"id": d["id"], "t": d["t"], "text": d["text"][:400]} for d in q],
            "next_at": next_at(conn, now), "gap_min": GAP_MS / 60_000, "per_day": RELEASE_PER_DAY}
