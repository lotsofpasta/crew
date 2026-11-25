"""Example agent: keeps an eye on your URLs. No LLM needed. Run: python examples/watchdog.py

  agents.yaml:  - {key: watchdog, name: Watchdog, role: Checks the sites stay up, reports_to: manager}
  .env:         WATCH_URLS=https://example.com,https://example.org/health   (WATCH_EVERY=60 seconds, WATCH_TIMEOUT=10)

Shows the issue lifecycle with real data:
  - a URL that fails twice in a row opens an issue (crit after 5 failures), with a proposed action
    "Mute <url> for 24h" that waits for your Approve
  - when the URL answers again the issue resolves itself, with how long it was down
  - each check is a 'check' pulse to the 'web' line, so the visualizer shows the work
Ask it anything on the exchange and it replies with the current status of every URL.
"""
import logging
import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from crew import config  # noqa: E402,F401  (loads .env)
from crew.agents.client import Agent  # noqa: E402

KEY = os.environ.get("WATCHDOG_KEY", "watchdog")
URLS = [u.strip() for u in os.environ.get("WATCH_URLS", "https://example.com").split(",") if u.strip()]
EVERY = int(os.environ.get("WATCH_EVERY", "60"))
TIMEOUT = float(os.environ.get("WATCH_TIMEOUT", "10"))

bot = Agent(KEY)
state = {u: {"fails": 0, "down_since": None, "last": None, "ms": None} for u in URLS}
muted = {}                     # url -> muted until (unix s)
last_round = 0.0


def check(url):
    t0 = time.time()
    try:
        r = httpx.get(url, timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": "crew-watchdog"})
        ok, detail = r.status_code < 400, f"HTTP {r.status_code}"
    except httpx.HTTPError as e:
        ok, detail = False, type(e).__name__
    return ok, detail, int((time.time() - t0) * 1000)


def key_of(url):
    return "down:" + url[:100]


@bot.on_tick
def round_(_res):
    global last_round
    if time.time() - last_round < EVERY:
        return
    last_round = time.time()
    down = 0
    for url in URLS:
        ok, detail, ms = check(url)
        s = state[url]
        s.update(last=detail, ms=ms)
        bot.activity("web", "check", f"{url} - {detail} in {ms} ms", meta={"url": url, "ok": ok, "ms": ms})
        if ok:
            if s["down_since"]:
                mins = max(1, round((time.time() - s["down_since"]) / 60))
                bot.resolve(key_of(url), f"{url} is back ({detail}) after about {mins} min.")
            s.update(fails=0, down_since=None)
            continue
        down += 1
        s["fails"] += 1
        s["down_since"] = s["down_since"] or time.time()
        if s["fails"] >= 2 and muted.get(url, 0) < time.time():
            bot.issue(key_of(url), f"{url} is down", f"Last answer: {detail}. Failed {s['fails']} checks in a row.",
                      level="crit" if s["fails"] >= 5 else "warn", facts={"url": url, "fails": s["fails"], "last": detail},
                      action={"kind": "mute", "url": url, "label": "Mute this URL for 24h"})
    bot.status(f"{len(URLS) - down} of {len(URLS)} up", urls=len(URLS), down=down)


@bot.on_approved
def approved(rep):
    action = rep.get("action") or {}
    if action.get("kind") == "mute" and action.get("url") in state:
        muted[action["url"]] = time.time() + 24 * 3600
        bot.action_done(rep["id"], True, f"Muted {action['url']} for 24 hours.")
    else:
        bot.action_done(rep["id"], False, "Unknown action.")


@bot.on_message
def status(m):
    lines = [f"{'up  ' if not s['fails'] else 'DOWN'} {u} ({s['last'] or 'not checked yet'}"
             f"{', %d ms' % s['ms'] if s['ms'] is not None else ''})" for u, s in state.items()]
    bot.say(m["src"], "Status:\n" + "\n".join(lines))


@bot.on_ticket
def ticket(t):
    bot.decline(t["id"], "I only watch URLs - add one to WATCH_URLS and I'll keep an eye on it.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    bot.run(every=10)
