"""The team, read from agents.yaml. Adding an agent = one entry there plus a sync token (python -m crew tokens)."""
import os
import re
from pathlib import Path

import yaml

from .. import config, db
from . import store

ALIVE_MS = 3 * 60_000          # an agent counts as on duty if it synced this recently
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
RESERVED = {"you", "all", "system"}
_cache = {"mtime": None, "path": None, "team": None}


def _load(path=None):
    path = Path(path or config.TEAM_FILE)
    mtime = path.stat().st_mtime if path.exists() else None
    if _cache["team"] is not None and _cache["mtime"] == mtime and _cache["path"] == path:
        return _cache["team"]
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    raw = raw or {}
    agents, seen = [], set()
    for a in raw.get("agents") or []:
        k = str(a.get("key", "")).strip().lower()
        if not KEY_RE.match(k) or k in RESERVED or k in seen:
            raise ValueError(f"agents.yaml: bad or duplicate agent key {k!r} (lowercase letters, digits, _; not {sorted(RESERVED)})")
        seen.add(k)
        agents.append({"name": k.title(), "role": "", "reports_to": "you", "about": "", "does": [], "uses": [], "limits": [],
                       **a, "key": k})
    lines = [{"key": "you", "label": "You"}] + [
        {"label": str(ln.get("key")).title(), **ln} for ln in raw.get("lines") or [] if ln.get("key") and ln.get("key") != "you"]
    manager = raw.get("manager") or (agents[0]["key"] if agents else None)
    team = {"agents": agents, "lines": lines, "manager": manager if manager in seen else None}
    _cache.update(mtime=mtime, path=path, team=team)
    return team


def team():
    return _load()["agents"]


def keys():
    return [a["key"] for a in team()]


def lines():
    return _load()["lines"]


def manager():
    return _load()["manager"]


def get(key):
    return next((a for a in team() if a["key"] == key), None)


def endpoints():
    """Every valid src / dst on the exchange: agent keys + line keys."""
    return set(keys()) | {ln["key"] for ln in lines()}


def token_env(key):
    return f"AGENT_TOKEN_{key.upper()}"


def token(key):
    t = os.environ.get(token_env(key), "")
    return t if len(t) >= 24 else None


def status(conn, key, now=None):
    now = now or db.now_ms()
    st = db.kv_get(conn, f"agent_status:{key}") or {}
    hb = st.get("heartbeat")
    return {**st, "heartbeat": hb, "alive": bool(hb and now - hb < ALIVE_MS)}


def roster(conn, now=None):
    """The team as the dashboard and the visualizer see it: agents.yaml fields + live status + report counts."""
    now = now or db.now_ms()
    store.init(conn)
    counts = store.counts(conn)
    return [{**a, **status(conn, a["key"], now), "slot": i + 1, "counts": counts.get(a["key"], {})}
            for i, a in enumerate(team())]
