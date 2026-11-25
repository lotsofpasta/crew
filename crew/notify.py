"""Optional Telegram alerts (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env). Without them every send is a no-op.

What the hub sends: critical issues and approval requests from agents, owner sign-ins from a new device or network,
and a burst of wrong passwords. Set TELEGRAM_LEVELS to change which report levels go out (default: crit,approval).
"""
import logging
import os
import threading

import httpx

log = logging.getLogger("crew.notify")
logging.getLogger("httpx").setLevel(logging.WARNING)      # request URLs would put the bot token in the logs


def enabled():
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def levels():
    return {x.strip() for x in os.environ.get("TELEGRAM_LEVELS", "crit,approval").split(",") if x.strip()}


def telegram(text):
    """Send a plain-text message. -> (ok, detail)."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False, "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set"
    try:
        d = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       json={"chat_id": chat, "text": str(text)[:4000], "disable_web_page_preview": True}, timeout=10).json()
        if not d.get("ok"):
            log.warning("telegram send failed: %s", d.get("description"))
            return False, d.get("description")
        return True, "sent"
    except (httpx.HTTPError, ValueError) as e:
        log.warning("telegram send failed: %s", e)
        return False, str(e)


def send_async(text):
    if enabled():
        threading.Thread(target=telegram, args=(text,), daemon=True).start()
