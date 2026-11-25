import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="crew-test-")
os.environ.update({
    "CREW_ENV_FILE": str(Path(_tmp) / "none.env"),       # never read the real .env
    "CREW_DATA_DIR": _tmp,
    "CREW_DB": str(Path(_tmp) / "test.db"),
    "AGENT_TOKEN_MANAGER": "m" * 32,
    "AGENT_TOKEN_RESEARCHER": "r" * 32,
    "LOCAL_ROLE": "viewer",
    "VIEWER_KEY": "v" * 30,
})
for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY"):
    os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crew import auth  # noqa: E402

os.environ["DASHBOARD_PASSWORD_HASH"] = auth.hash_password("correct horse battery", n=2 ** 10)
