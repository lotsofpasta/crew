"""Example agent: works the tickets on its desk and hands back a written answer. Run: python examples/researcher.py

With ANTHROPIC_API_KEY set it answers with Claude; without it, it hands back a placeholder so you can see the flow.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crew.agents import brain as brainmod  # noqa: E402
from crew.agents.client import Agent  # noqa: E402

KEY = "researcher"
SYSTEM = """You are the team's researcher. You get one task at a time from a teammate or the owner. Answer it as
well as you can from what you know, say plainly what you are unsure of, and keep it short.
Reply with one JSON object matching the given schema."""
ANSWER = {"type": "object", "additionalProperties": False, "required": ["take_it", "title", "answer"],
          "properties": {"take_it": {"type": "boolean"}, "title": {"type": "string"}, "answer": {"type": "string"}}}

bot = Agent(KEY)
brain = bot.brain(SYSTEM, daily_calls=60) if brainmod.available() else None


@bot.on_ticket
def work(t):
    bot.status(f"working on #{t['id']}")
    task = t["title"] + ("\n\n" + t["body"] if t.get("body") else "")
    if not brain:
        bot.accept(t["id"], "On it (no LLM key, so this will be a placeholder).")
        bot.finish(t["id"], "Placeholder answer", f"Set ANTHROPIC_API_KEY to get a real answer to:\n{task}")
        return
    try:
        r = brain.ask(f"Task from {t['src']}:\n{task}", ANSWER)
    except Exception as e:
        bot.decline(t["id"], f"I couldn't work on this right now ({type(e).__name__}).")
        return
    if not r["take_it"]:
        bot.decline(t["id"], r["answer"][:300])
        return
    bot.accept(t["id"])
    bot.finish(t["id"], r["title"], r["answer"])
    bot.status("on duty")


@bot.on_message
def chat(m):
    if m["kind"] == "message":
        bot.say(m["src"], "I work from tickets - file one and I'll pick it up.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    bot.run()
