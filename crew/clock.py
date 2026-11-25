"""Time units in milliseconds (every timestamp is Unix ms) and the owner's clock (CREW_TZ, default UTC).

Daily counters (LLM budgets, ticket caps, drop pacing) roll over at midnight in CREW_TZ."""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config

MIN_MS = 60_000
HOUR_MS = 60 * MIN_MS
DAY_MS = 24 * HOUR_MS

TZ = ZoneInfo(config.TIMEZONE)


def now_ms() -> int:
    return int(time.time() * 1000)


def local(ms=None):
    return datetime.fromtimestamp((ms if ms is not None else now_ms()) / 1000, TZ)


def local_day(ms=None):
    """'YYYY-MM-DD' in the owner's timezone - the key for every daily counter."""
    return local(ms).strftime("%Y-%m-%d")
