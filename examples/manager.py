"""Example agent: the team lead. Run: python examples/manager.py

Shows every part of the protocol:
  - answers the owner's messages and drops (with Claude if ANTHROPIC_API_KEY is set, else a simple rule)
  - hands work to a teammate as a ticket, and passes the answer back to the owner
  - opens an issue when a teammate goes quiet, with a proposed action that waits for the owner's Approve,
    carries the action out once approved, and resolves the issue when the teammate is back
Copy this file to start your own agent.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crew.agents import brain as brainmod  # noqa: E402
from crew.agents.client import Agent  # noqa: E402

KEY = "manager"
SYSTEM = """You are the team lead of a small team of AI agents, working for one owner. You read what the owner sends
and decide: answer it yourself, or hand it to a teammate as a ticket (name the teammate's key). Be brief and plain.
Reply with one JSON object matching the given schema."""
ROUTE = {"type": "object", "additionalProperties": False, "required": ["reply", "delegate_to", "ticket_title", "ticket_body"],
         "properties": {"reply": {"type": "string"}, "delegate_to": {"type": "string"},
                        "ticket_title": {"type": "string"}, "ticket_body": {"type": "string"}}}

bot = Agent(KEY)
brain = bot.brain(SYSTEM, daily_calls=60) if brainmod.available() else None
quiet_since = {}


def teammates():
    return [t for t in bot.team if t["key"] != KEY]


def decide(text):
    """-> (reply, delegate_to or None, title, body)"""
    mates = teammates()
    if brain:
        roster = "\n".join(f"- {t['key']}: {t['role']}" for t in mates) or "(no teammates)"
        try:
            r = brain.ask(f"Teammates:\n{roster}\n\nThe owner wrote:\n{text}", ROUTE, light=True)
            to = r["delegate_to"] if r["delegate_to"] in {t["key"] for t in mates} else None
            return r["reply"], to, r["ticket_title"] or text[:100], r["ticket_body"] or text
        except Exception as e:          # budget spent, refusal, network: fall back to the rule
            logging.warning("LLM routing failed: %s", e)
    to = mates[0]["key"] if mates and ("?" in text or "http" in text) else None
    return ("Passing this to " + to + "." if to else "Noted, thanks."), to, text[:100], text


@bot.on_message
def handle(m):
    if m["src"] == "you" and m["kind"] in ("message", "drop"):
        bot.status("reading your message")
        text = m.get("text") or m["label"]
        reply, to, title, body = decide(text)
        bot.say("you", reply)
        if to:
            bot.ticket(to, title, body)
    elif m["kind"] == "deliverable" and m["src"] != "you":
        # a teammate finished a ticket we filed: pass it up
        bot.deliver(f"From {m['src']}: {m['label']}", m.get("text") or m["label"], key=f"relay:{m['id']}")
    elif m["kind"] == "decline":
        bot.say("you", f"{m['src']} declined a task: {m['label']}")
    bot.status("on duty")


@bot.on_ticket
def ticket(t):
    bot.accept(t["id"], "On it.")
    reply, _, _, _ = decide(t["title"] + "\n" + (t.get("body") or ""))
    bot.finish(t["id"], "Answer", reply)


@bot.on_approved
def approved(rep):
    action = rep.get("action") or {}
    if action.get("kind") == "nudge":
        bot.say(action["agent"], "The owner asked me to check on you - are you running?")
        bot.action_done(rep["id"], True, f"Nudged {action['agent']}.")
    else:
        bot.action_done(rep["id"], False, f"I don't know how to do {action.get('kind')!r}.")


@bot.on_tick
def watch(_res):
    for t in teammates():
        key = f"quiet:{t['key']}"
        if not t["alive"]:
            if t["key"] not in quiet_since:
                quiet_since[t["key"]] = True
                bot.issue(key, f"{t['name']} has gone quiet", f"No sync from {t['name']} for a few minutes.", level="warn",
                          action={"kind": "nudge", "agent": t["key"], "label": f"Nudge {t['name']}"})
        elif quiet_since.pop(t["key"], None):
            bot.resolve(key, f"{t['name']} is back.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    bot.run(every=10)
