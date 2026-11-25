"""Dashboard sign-in.

- The owner password is never stored in plain text: DASHBOARD_PASSWORD_HASH holds an scrypt hash
  (set it with `python -m crew setpass`). A login trades the password for a random session token; the browser keeps
  only that token, the server only its SHA-256.
- Optional view-only access: VIEWER_PASSWORD_HASH (a second password) or VIEWER_KEY (a long random link key).
- Logins are rate-limited per address and globally.
- An owner sign-in from a new device or network is reported (Telegram, if configured).
"""
import base64
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import time

from . import db

SESSION_DAYS = 30
IP_FAILS, GLOBAL_FAILS, FAIL_WINDOW_S = 5, 20, 15 * 60
MIN_PASSWORD = 12
MIN_VIEWER_KEY = 24

_fails = {}            # ip -> [times]
_global_fails = []     # times
_alerted = {"t": 0.0}


def init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS auth_sessions (hash TEXT PRIMARY KEY, role TEXT, created INTEGER,
                    expires INTEGER, last_seen INTEGER, ip TEXT, ua TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS auth_known (kind TEXT, key TEXT, first INTEGER, last INTEGER,
                    PRIMARY KEY (kind, key))""")
    conn.commit()


# ------------------------------------------------------------------------------------------------ known devices
def login_net(ip):
    """The network a sign-in came from: its /24 (IPv4) or /48 (IPv6)."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ip or "?"
    return str(ipaddress.ip_network(f"{ip}/{24 if a.version == 4 else 48}", strict=False))


def login_device(ua):
    """Browser + system with version numbers dropped, so an update is still the same device."""
    return re.sub(r"\s+", " ", re.sub(r"[\d_.]+", "", ua or "")).strip()[:160] or "?"


def owner_login_seen(conn, ip, ua, now_ms=None):
    """Record an owner sign-in. -> (new_network, new_device). The very first sign-in is not reported as new."""
    init(conn)
    now_ms = now_ms or db.now_ms()
    first_ever = not conn.execute("SELECT 1 FROM auth_known LIMIT 1").fetchone()
    new = []
    for kind, key in (("net", login_net(ip)), ("device", login_device(ua))):
        seen = conn.execute("SELECT 1 FROM auth_known WHERE kind=? AND key=?", (kind, key)).fetchone()
        new.append(not seen and not first_ever)
        conn.execute("INSERT INTO auth_known VALUES (?,?,?,?) ON CONFLICT(kind, key) DO UPDATE SET last=excluded.last",
                     (kind, key, now_ms, now_ms))
    conn.commit()
    return tuple(new)


def _h(token):
    return hashlib.sha256(token.encode()).hexdigest()


# ------------------------------------------------------------------------------------------------ passwords
def hash_password(pw, n=2 ** 15, r=8, p=1):
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=n, r=r, p=p, maxmem=2 ** 26, dklen=32)
    return "scrypt${}${}${}${}${}".format(n, r, p, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def check_password(pw, stored):
    try:
        alg, n, r, p, salt, dk = stored.split("$")
        if alg != "scrypt":
            return False
        got = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), maxmem=2 ** 26,
                             dklen=len(base64.b64decode(dk)))
        return hmac.compare_digest(got, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


def owner_login_ready():
    return os.environ.get("DASHBOARD_PASSWORD_HASH", "").startswith("scrypt$")


def owner_password_ok(pw):
    return bool(pw) and owner_login_ready() and check_password(pw, os.environ["DASHBOARD_PASSWORD_HASH"])


def viewer_password_ok(pw):
    stored = os.environ.get("VIEWER_PASSWORD_HASH", "")
    return bool(pw) and stored.startswith("scrypt$") and check_password(pw, stored)


def viewer_key():
    k = os.environ.get("VIEWER_KEY", "")
    return k if len(k) >= MIN_VIEWER_KEY else None


# ------------------------------------------------------------------------------------------------ login limits
def _recent(ts, now):
    return [t for t in ts if now - t < FAIL_WINDOW_S]


def login_blocked(ip, now=None):
    now = now or time.time()
    _global_fails[:] = _recent(_global_fails, now)
    if len(_global_fails) >= GLOBAL_FAILS:
        return "too many wrong passwords from everywhere - logins are paused for 15 minutes"
    if len(_recent(_fails.get(ip, []), now)) >= IP_FAILS:
        return "too many wrong passwords - try again in 15 minutes"
    return None


def login_failed(ip, now=None):
    """Count a failure. -> True when the global limit is hit (worth an alert)."""
    now = now or time.time()
    _fails[ip] = _recent(_fails.get(ip, []), now) + [now]
    _global_fails.append(now)
    for k in [k for k, v in _fails.items() if not _recent(v, now)]:
        _fails.pop(k, None)
    return len(_recent(_global_fails, now)) >= GLOBAL_FAILS


def reset_limits():
    _fails.clear()
    _global_fails.clear()
    _alerted["t"] = 0.0


# ------------------------------------------------------------------------------------------------ sessions
def new_session(conn, role, ip="", ua="", now_ms=None):
    init(conn)
    now_ms = now_ms or db.now_ms()
    token = "s_" + secrets.token_urlsafe(32)
    conn.execute("INSERT INTO auth_sessions(hash, role, created, expires, last_seen, ip, ua) VALUES (?,?,?,?,?,?,?)",
                 (_h(token), role, now_ms, now_ms + SESSION_DAYS * 86_400_000, now_ms, ip[:64], ua[:200]))
    conn.commit()
    return token


def session_role(conn, token, now_ms=None):
    if not token.startswith("s_"):
        return None
    init(conn)
    now_ms = now_ms or db.now_ms()
    row = conn.execute("SELECT role, expires, last_seen FROM auth_sessions WHERE hash=?", (_h(token),)).fetchone()
    if not row or row[1] < now_ms:
        return None
    if now_ms - (row[2] or 0) > 3_600_000:
        conn.execute("UPDATE auth_sessions SET last_seen=? WHERE hash=?", (now_ms, _h(token)))
        conn.commit()
    return row[0]


def end_session(conn, token, everywhere=False):
    init(conn)
    n = (conn.execute("DELETE FROM auth_sessions").rowcount if everywhere
         else conn.execute("DELETE FROM auth_sessions WHERE hash=?", (_h(token),)).rowcount)
    conn.commit()
    return n
