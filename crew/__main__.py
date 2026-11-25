"""python -m crew <command>

  serve            run the hub (dashboard + exchange + visualizer + agent sync) on HOST:PORT (default 127.0.0.1:8000)
  setpass          set the owner password (stored as an scrypt hash in .env) and make a view-only key
  tokens           give every agent in agents.yaml a sync token in .env (keeps existing ones)
  demo [minutes]   fill the exchange with made-up traffic so you can design the visualizer without real agents
  check            print what is configured and what is missing
"""
import getpass
import os
import secrets
import sys

from . import config


def _env_path():
    return config.ROOT / ".env" if not os.environ.get("CREW_ENV_FILE") else __import__("pathlib").Path(os.environ["CREW_ENV_FILE"])


def _read_env():
    p = _env_path()
    return p.read_text().splitlines() if p.exists() else []


def _write_env(lines):
    p = _env_path()
    old = os.umask(0o077)
    try:
        p.write_text("\n".join(lines).rstrip("\n") + "\n")
    finally:
        os.umask(old)
    os.chmod(p, 0o600)


def _set(lines, key, value):
    out = [ln for ln in lines if not ln.startswith(key + "=")]
    out.append(f"{key}={value}")
    return out


def setpass():
    from .auth import MIN_PASSWORD, MIN_VIEWER_KEY, hash_password
    pw = getpass.getpass(f"New owner password ({MIN_PASSWORD}+ characters; a few random words is good): ")
    if len(pw) < MIN_PASSWORD:
        sys.exit(f"Too short - use at least {MIN_PASSWORD} characters.")
    if getpass.getpass("Again: ") != pw:
        sys.exit("They don't match.")
    lines = _set(_read_env(), "DASHBOARD_PASSWORD_HASH", hash_password(pw))
    if not any(ln.startswith("VIEWER_KEY=") and len(ln.split("=", 1)[1].strip()) >= MIN_VIEWER_KEY for ln in lines):
        lines = _set(lines, "VIEWER_KEY", secrets.token_urlsafe(24))
        print("Made a view-only key (VIEWER_KEY in .env) - share it for read-only access.")
    _write_env(lines)
    print(f"Saved the password hash to {_env_path()}. Restart the hub; every device logs in once more.")


def tokens():
    from .agents import registry
    lines = _read_env()
    have = {ln.split("=", 1)[0] for ln in lines if "=" in ln and len(ln.split("=", 1)[1].strip()) >= 24}
    made = []
    for k in registry.keys():
        name = registry.token_env(k)
        if name not in have:
            lines = _set(lines, name, secrets.token_urlsafe(32))
            made.append(k)
    _write_env(lines)
    print(f"New tokens for: {', '.join(made)}" if made else "Every agent already has a token.")
    print(f"Tokens are in {_env_path()}. An agent on another machine needs only its own AGENT_TOKEN_<KEY> and CREW_URL.")


def check():
    from . import auth
    from .agents import brain, registry
    ok = lambda b: "ok " if b else "-- "  # noqa: E731
    print(f"{ok(True)}brand: {config.BRAND_NAME}   database: {config.DB_PATH}   timezone: {config.TIMEZONE}")
    print(f"{ok(auth.owner_login_ready())}owner password (python -m crew setpass)")
    print(f"{ok(brain.available())}LLM key (ANTHROPIC_API_KEY) - without it the example agents answer with simple rules")
    from . import notify
    print(f"{ok(notify.enabled())}Telegram alerts (optional)")
    for a in registry.team():
        print(f"{ok(registry.token(a['key']))}agent {a['key']:<14} token ({registry.token_env(a['key'])})")


def main(argv):
    cmd = argv[0] if argv else "help"
    if cmd == "serve":
        from .server import main as serve
        serve()
    elif cmd == "setpass":
        setpass()
    elif cmd == "tokens":
        tokens()
    elif cmd == "check":
        check()
    elif cmd == "demo":
        from .demo import run
        run(float(argv[1]) if len(argv) > 1 else 0)
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
