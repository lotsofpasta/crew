"""SQLite storage (WAL mode, so the server's threads and a CLI can share it).

Core tables live here (kv, events). Each agent module creates its own tables in its init():
  agents/store.py    agent_reports   issues, findings, deliverables, notes, approval requests
  agents/signals.py  agent_signals   every message / activity on the exchange (the visualizer's raw feed)
  agents/tickets.py  agent_tickets   work requests between agents (and from the owner)
  agents/drops.py    owner_drops     links / notes the owner drops for the team, released at a steady pace
  auth.py            auth_*          sessions and known sign-in devices
"""
import json
import sqlite3
import threading
from pathlib import Path

from . import config
from .clock import now_ms  # noqa: F401  (re-exported: db.now_ms())

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, t INTEGER, level TEXT, kind TEXT, title TEXT, body TEXT);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def kv_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def kv_set(conn, key, value):
    conn.execute("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, json.dumps(value)))
    conn.commit()


def rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def event(conn, level, kind, title, body=""):
    """One line in the dashboard's activity log (logins, approvals, agent starts...)."""
    conn.execute("INSERT INTO events(t, level, kind, title, body) VALUES (?,?,?,?,?)", (now_ms(), level, kind, title, body))
    conn.commit()


class ThreadLocalConn:
    """One sqlite connection per thread (FastAPI runs sync routes in a thread pool), used like a connection."""

    def __init__(self, path=None):
        self._local, self._path = threading.local(), path

    def _conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._local.conn = connect(self._path)
        return c

    def __getattr__(self, name):
        return getattr(self._conn(), name)
