"""Agent reports: everything an agent hands the owner, in one feed (the dashboard's Agents page).

kind   'issue'       a problem; lifecycle open -> resolved (by the agent when the cause clears, or by the owner)
       'finding'     an observation; same lifecycle - open while the agent keeps repeating it
       'deliverable' finished work (a memo, a digest, an answer to a ticket). No lifecycle; unread until seen.
       'note'        a status update. No lifecycle.
key    the agent's stable name for the issue (e.g. 'teammate-offline:researcher'); one open issue per (agent, key)
level  crit / warn / info. A level going up counts as new: it clears `seen_t` and is announced again.
action an optional step the agent proposes: {"kind": "<anything>", "label": "Restart the scraper", ...}.
       action_state: proposed (waits for the owner's Approve) -> approved -> done / failed; rejected; expired.
       The agent sees approved actions on its next sync, carries them out, and reports done / failed.
"""
import json

from .. import clock, db
from . import signals

LEVELS = {"info": 0, "warn": 1, "crit": 2}
LIVE_KINDS = ("issue", "finding")
KINDS = ("issue", "finding", "deliverable", "note")

SCHEMA = """CREATE TABLE IF NOT EXISTS agent_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'issue', key TEXT NOT NULL,
    t INTEGER, updated_t INTEGER, level TEXT, status TEXT, title TEXT, body TEXT, facts TEXT, ref TEXT,
    action TEXT, action_state TEXT, action_t INTEGER, action_note TEXT, seen_t INTEGER, resolved_t INTEGER, resolution TEXT);
CREATE INDEX IF NOT EXISTS agent_reports_open ON agent_reports(agent, status, key);
CREATE INDEX IF NOT EXISTS agent_reports_feed ON agent_reports(kind, id);"""


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()
    signals.init(conn)


def _row(r):
    d = dict(r)
    for k in ("facts", "action"):
        d[k] = json.loads(d[k]) if d.get(k) else None
    return d


def get(conn, rid):
    r = conn.execute("SELECT * FROM agent_reports WHERE id=?", (rid,)).fetchone()
    return _row(r) if r else None


def find_open(conn, agent, key):
    r = conn.execute("SELECT * FROM agent_reports WHERE agent=? AND key=? AND kind IN ('issue','finding') AND status='open'",
                     (agent, key)).fetchone()
    return _row(r) if r else None


def upsert(conn, agent, key, level, title, body="", facts=None, action=None, now=None, kind="issue", ref=None):
    """Open the issue (or finding), or update the open one with the same key. -> (report, change): change is 'new',
    'up' (level rose - announce again) or None (same issue, details refreshed)."""
    assert kind in LIVE_KINDS, kind
    level = level if level in LEVELS else "info"
    now = now or db.now_ms()
    cur = find_open(conn, agent, key)
    fj, aj = json.dumps(facts) if facts is not None else None, json.dumps(action) if action else None
    if cur is None:
        rid = conn.execute("INSERT INTO agent_reports(agent, kind, key, t, updated_t, level, status, title, body, facts, action,"
                           " action_state, ref) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)",
                           (agent, kind, key, now, now, level, title, body, fj, aj, "proposed" if action else None, ref)).lastrowid
        conn.commit()
        _say(conn, agent, kind, level, title, body, rid, bool(action), now)
        return get(conn, rid), "new"
    up = LEVELS[level] > LEVELS.get(cur["level"], 0)
    sets = dict(updated_t=now, level=level, title=title, body=body, facts=fj)
    if up:
        sets["seen_t"] = None
    if action and not cur["action"]:
        sets.update(action=aj, action_state="proposed")
    conn.execute(f"UPDATE agent_reports SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), cur["id"]))
    conn.commit()
    if up or (action and not cur["action"]):
        _say(conn, agent, kind, level, title, body, cur["id"], bool(action and not cur["action"]), now)
    return get(conn, cur["id"]), "up" if up else None


def _say(conn, agent, kind, level, title, body, rid, asks, now):
    """An issue reaching the owner is a message on the exchange; one carrying a proposed action is an approval request."""
    signals.emit(conn, agent, "you", "approval" if asks else kind, title, body, level=level, ref=f"report:{rid}", chat=True, t=now)


def resolve(conn, rid, resolution, now=None):
    cur = get(conn, rid)
    if not cur or cur["status"] != "open":
        return False
    signals.emit(conn, cur["agent"], "you", "resolve", f"cleared: {cur['title']}", resolution, ref=f"report:{rid}", t=now,
                 commit=False)
    conn.execute("UPDATE agent_reports SET status='resolved', resolved_t=?, resolution=?, "
                 "action_state=CASE WHEN action_state='proposed' THEN 'expired' ELSE action_state END WHERE id=? AND status='open'",
                 (now or db.now_ms(), resolution, rid))
    conn.commit()
    return True


def resolve_key(conn, agent, key, resolution, now=None):
    cur = find_open(conn, agent, key)
    return resolve(conn, cur["id"], resolution, now) if cur else False


def set_action(conn, rid, state, note=None, now=None):
    conn.execute("UPDATE agent_reports SET action_state=?, action_t=?, action_note=COALESCE(?, action_note) WHERE id=?",
                 (state, now or db.now_ms(), note, rid))
    conn.commit()


def note(conn, agent, key, title, body, facts=None, level="info", now=None, kind="note", ref=None, to="you"):
    """A status update (kind 'note') or a deliverable: stored resolved, so it never shows as an open issue; unread
    until the owner marks it seen. `to`: who it is addressed to on the exchange chat."""
    assert kind in ("note", "deliverable")
    now = now or db.now_ms()
    rid = conn.execute("INSERT INTO agent_reports(agent, kind, key, t, updated_t, level, status, title, body, facts, resolved_t, ref)"
                       " VALUES (?, ?, ?, ?, ?, ?, 'resolved', ?, ?, ?, ?, ?)",
                       (agent, kind, key, now, now, level, title, body, json.dumps(facts) if facts is not None else None, now,
                        ref)).lastrowid
    conn.commit()
    signals.emit(conn, agent, to, kind, title, body, level=level, ref=ref or f"report:{rid}", chat=True, t=now)
    return rid


def deliver(conn, agent, key, title, body, facts=None, ref=None, now=None, to="you"):
    return note(conn, agent, key, title, body, facts, now=now, kind="deliverable", ref=ref, to=to)


def approvals(conn):
    """Everything waiting for the owner's Approve / Reject, oldest first."""
    return [_row(r) for r in conn.execute("SELECT * FROM agent_reports WHERE status='open' AND action_state='proposed' ORDER BY id")]


def approved_for(conn, agent):
    """Actions the owner approved that `agent` has not carried out yet (it gets them on each sync)."""
    return [_row(r) for r in conn.execute("SELECT * FROM agent_reports WHERE agent=? AND action_state='approved' ORDER BY id", (agent,))]


def feed(conn, agent=None, kind=None, before=None, limit=40, unread=False):
    """History, newest first, with paging."""
    q, p = "SELECT * FROM agent_reports WHERE 1=1", []
    if agent:
        q += " AND agent=?"; p.append(agent)
    if kind:
        q += " AND kind=?"; p.append(kind)
    if before:
        q += " AND id<?"; p.append(int(before))
    if unread:
        q += " AND seen_t IS NULL"
    q += " ORDER BY id DESC LIMIT ?"; p.append(max(1, min(int(limit), 200)))
    return [_row(r) for r in conn.execute(q, p).fetchall()]


def counts(conn):
    """Per agent: open issues / findings, waiting approvals, unread items."""
    out = {}
    for r in conn.execute("""SELECT agent,
            SUM(kind='issue' AND status='open'), SUM(kind='issue' AND status='open' AND level='crit'),
            SUM(kind='finding' AND status='open'), SUM(status='open' AND action_state='proposed'),
            SUM(kind='deliverable' AND seen_t IS NULL), SUM(kind IN ('finding','issue') AND status='open' AND seen_t IS NULL),
            MAX(t) FROM agent_reports GROUP BY agent"""):
        out[r[0]] = {"issues": r[1] or 0, "crit": r[2] or 0, "findings": r[3] or 0, "approvals": r[4] or 0,
                     "unread_deliverables": r[5] or 0, "unread": r[6] or 0, "last_t": r[7]}
    return out


def checkin(conn, agent, doing=None, now=None, **stats):
    """A heartbeat: what the agent is doing now plus a few numbers for its card."""
    cur = db.kv_get(conn, f"agent_status:{agent}") or {}
    cur.update({k: v for k, v in stats.items() if v is not None}, heartbeat=now or db.now_ms())
    if doing is not None:
        cur["doing"] = str(doing)[:200]
    db.kv_set(conn, f"agent_status:{agent}", cur)


def recent(conn, hours=48, now=None):
    """Every open issue, issues resolved in the last `hours`, and the latest notes and deliverables."""
    now = now or db.now_ms()
    since = now - hours * clock.HOUR_MS
    rows = conn.execute("SELECT * FROM agent_reports WHERE (kind IN ('issue','finding') AND (status='open' OR resolved_t >= ?))"
                        " OR id IN (SELECT id FROM agent_reports WHERE kind IN ('note','deliverable') ORDER BY id DESC LIMIT 10)"
                        " ORDER BY id DESC LIMIT 300", (since,)).fetchall()
    return [_row(r) for r in rows]


def prune(conn, days=90, now=None, keep_deliverables_days=365):
    now = now or db.now_ms()
    n = conn.execute("DELETE FROM agent_reports WHERE status='resolved' AND ((kind!='deliverable' AND resolved_t < ?)"
                     " OR (kind='deliverable' AND resolved_t < ?))",
                     (now - days * clock.DAY_MS, now - keep_deliverables_days * clock.DAY_MS)).rowcount
    conn.commit()
    signals.prune(conn, now)
    return n
