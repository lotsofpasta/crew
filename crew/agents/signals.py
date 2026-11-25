"""The signal log: every time an agent does something or talks to someone, one row. This is the raw feed for the
Exchange page and for any visualizer you build: draw each row as a pulse from `src` to `dst`.

src / dst  an agent key from agents.yaml, or a "line" (an outside endpoint: 'you' = the owner, 'llm', 'web', ...;
           the list is `lines:` in agents.yaml - 'you' is always there)
kind       free-form short word. Kinds the core emits: message, issue, finding, approval, resolve, approve, reject,
           note, deliverable, ticket, accept, decline, drop, action, llm. Agents may add their own (search, fetch, ...).
chat       1 = a message someone wrote to someone (shown in the chat); 0 = activity (visualizer / tape only)
ref        a link to what the row is about: 'report:<id>', 'ticket:<id>', 'drop:<id>'
meta       optional JSON for your visualizer (anything the agent attached)
Check-ins (heartbeats) are not signals: a visualizer can blink an agent when its heartbeat moves.
"""
import json

from .. import db

KEEP_DAYS = 14
SCHEMA = """CREATE TABLE IF NOT EXISTS agent_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, t INTEGER NOT NULL, src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL,
    label TEXT, text TEXT, level TEXT, ref TEXT, chat INTEGER NOT NULL DEFAULT 0, meta TEXT);
CREATE INDEX IF NOT EXISTS agent_signals_t ON agent_signals(t);
CREATE INDEX IF NOT EXISTS agent_signals_dst ON agent_signals(dst, id);"""


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def emit(conn, src, dst, kind, label, text=None, level=None, ref=None, chat=False, t=None, meta=None, commit=True):
    """Write one signal. Returns its id, or None. Never raises: a signal must not break the agent that sends it."""
    try:
        cur = conn.execute(
            "INSERT INTO agent_signals(t, src, dst, kind, label, text, level, ref, chat, meta) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (t or db.now_ms(), str(src)[:32], str(dst)[:32], str(kind)[:24], (label or "")[:300],
             (text or None) and str(text)[:4000], level, ref, 1 if chat else 0, json.dumps(meta) if meta else None))
        if commit:
            conn.commit()
        return cur.lastrowid
    except Exception:
        try:
            init(conn)
        except Exception:
            pass
        return None


def _row(r):
    d = dict(r)
    d["chat"] = bool(d["chat"])
    d["meta"] = json.loads(d["meta"]) if d.get("meta") else None
    return d


def since(conn, after=0, limit=300, chat=None):
    """Signals with id > after, oldest first (a page polls with the last id it has)."""
    q, p = "SELECT * FROM agent_signals WHERE id>?", [after]
    if chat is not None:
        q += " AND chat=?"
        p.append(1 if chat else 0)
    return [_row(r) for r in conn.execute(q + " ORDER BY id LIMIT ?", (*p, limit)).fetchall()]


def recent(conn, limit=200, chat=None, before=None, src=None):
    """The newest signals, returned oldest first (first page load, paging back, one speaker's messages)."""
    q, p = "SELECT * FROM agent_signals WHERE 1=1", []
    if src:
        q += " AND src=?"
        p.append(src)
    if chat is not None:
        q += " AND chat=?"
        p.append(1 if chat else 0)
    if before:
        q += " AND id<?"
        p.append(before)
    rows = conn.execute(q + " ORDER BY id DESC LIMIT ?", (*p, limit)).fetchall()
    return [_row(r) for r in reversed(rows)]


def inbox(conn, agent, after=0, limit=100):
    """Chat messages addressed to `agent` after id `after` - what an agent reads on each sync."""
    return [_row(r) for r in conn.execute(
        "SELECT * FROM agent_signals WHERE dst=? AND chat=1 AND id>? AND src!=? ORDER BY id LIMIT ?",
        (agent, after, agent, limit)).fetchall()]


def window(conn, t0, t1, limit=5000):
    """Everything between two times, for replay."""
    return [_row(r) for r in conn.execute("SELECT * FROM agent_signals WHERE t>=? AND t<? ORDER BY t, id LIMIT ?",
                                          (t0, t1, limit)).fetchall()]


def last_id(conn):
    r = conn.execute("SELECT MAX(id) FROM agent_signals").fetchone()
    return r[0] or 0


def prune(conn, now=None):
    conn.execute("DELETE FROM agent_signals WHERE t<?", ((now or db.now_ms()) - KEEP_DAYS * 86_400_000,))
    conn.commit()
