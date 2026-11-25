"""Settings: environment variables (from .env) plus the team file agents.yaml.

Everything a user is expected to change lives in two places:
  .env         secrets and deployment settings (see .env.example)
  agents.yaml  the team: who the agents are, who reports to whom, the "lines" (outside services) on the exchange
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path=None):
    """Minimal .env loader (KEY=VALUE lines, # comments). Variables already in the environment win."""
    path = Path(path or os.environ.get("CREW_ENV_FILE") or ROOT / ".env")
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()


def env(key, default=None):
    v = os.environ.get(key)
    return v if v not in (None, "") else default


BRAND_NAME = env("BRAND_NAME", "Crew")
BRAND_TAGLINE = env("BRAND_TAGLINE", "Your agents, in one room.")
DATA_DIR = Path(env("CREW_DATA_DIR", ROOT / "data"))
DB_PATH = Path(env("CREW_DB", DATA_DIR / "crew.db"))
TIMEZONE = env("CREW_TZ", "UTC")
PUBLIC_URL = (env("PUBLIC_URL", "http://127.0.0.1:8000") or "").rstrip("/")
TEAM_FILE = Path(env("CREW_TEAM_FILE", ROOT / "agents.yaml"))
