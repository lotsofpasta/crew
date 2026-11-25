"""Made-up traffic for designing a visualizer without running real agents: writes straight to the database.

python -m crew demo        one burst of history (the last hour), then exit
python -m crew demo 30     ...then keep generating live traffic for 30 minutes (Ctrl-C to stop)

It uses the agents in agents.yaml. Everything it writes is ordinary data (signals, reports, tickets), so the
dashboard, the exchange and /api/viz show it exactly as they would show real agents.
"""
import random
import time

from . import db
from .agents import registry, signals, store, tickets

LINES_KINDS = {"llm": ["llm"], "web": ["search", "fetch"], "telegram": ["telegram"]}
PHRASES = ["Looking into it now.", "Here is what I found.", "Can you double-check this?", "Done - summary attached.",
           "Waiting on the data.", "Two options, I recommend the first.", "No change since this morning.",
           "This needs the owner's call.", "Filed it.", "On it."]


def _beat(conn, agents, now):
    for a in agents:
        if random.random() < 0.9:
            store.checkin(conn, a, random.choice(["reading", "thinking", "writing a summary", "idle", "checking the queue"]), now)


def _one(conn, agents, lines, now, history=False):
    a = random.choice(agents)
    r = random.random()
    if r < 0.45:
        ln = random.choice(lines) if lines else "llm"
        kind = random.choice(LINES_KINDS.get(ln, ["activity"]))
        signals.emit(conn, a, ln, kind, f"{kind} ({a})", t=now, meta={"demo": True})
        signals.emit(conn, ln, a, kind, f"{kind} result", t=now + random.randint(300, 4000), meta={"demo": True})
    elif r < 0.75:
        b = random.choice([x for x in agents + ["you"] if x != a])
        signals.emit(conn, a, b, "message", random.choice(PHRASES), chat=True, t=now)
    elif r < 0.85 and len(agents) > 1:
        b = random.choice([x for x in agents if x != a])
        t, new = tickets.file(conn, a, b, random.choice(["Summarise today's notes", "Find three sources on the topic",
                                                         "Check the numbers in the last memo"]), now=now)
        if t and new:
            tickets.update(conn, t["id"], "accepted", "On it.", now=now + 1000)
            if history or random.random() < 0.5:
                tickets.finish(conn, t["id"], "Done", "A short made-up answer for the demo.", now=now + 5000)
    elif r < 0.93:
        store.upsert(conn, a, f"demo:{random.randint(1, 4)}", random.choice(["info", "warn", "crit"]), "Something to look at (demo)",
                     "Made-up issue so the attention states have something to show.", now=now,
                     action={"kind": "demo", "label": "Try the fix (demo)"} if random.random() < 0.3 else None)
    else:
        for rep in store.feed(conn, a, "issue", limit=5):
            if rep["status"] == "open":
                store.resolve(conn, rep["id"], "Cleared (demo).", now)
                break


def run(minutes=0):
    conn = db.connect()
    store.init(conn)
    tickets.init(conn)
    agents = registry.keys()
    if not agents:
        raise SystemExit("agents.yaml has no agents")
    lines = [ln["key"] for ln in registry.lines() if ln["key"] != "you"]
    now = db.now_ms()
    for i in range(120):                                      # an hour of history
        _one(conn, agents, lines, now - (120 - i) * 30_000, history=True)
    _beat(conn, agents, now)
    print(f"Wrote an hour of demo traffic for {', '.join(agents)}. Open /exchange or /visualizer.")
    end = time.time() + minutes * 60
    while time.time() < end:
        now = db.now_ms()
        for _ in range(random.randint(1, 3)):
            _one(conn, agents, lines, now)
        _beat(conn, agents, now)
        time.sleep(random.uniform(1.0, 3.0))
