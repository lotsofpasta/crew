"""The smallest useful agent: answers chat messages and finishes tickets. Copy this to start a new one.

1. add it to agents.yaml:   - {key: echo, name: Echo, role: Says things back}
2. python -m crew tokens
3. python examples/minimal.py echo
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from crew.agents.client import Agent  # noqa: E402

bot = Agent(sys.argv[1] if len(sys.argv) > 1 else "echo")


@bot.on_message
def reply(m):                                   # m: a chat signal - src, kind, label, text
    bot.say(m["src"], f"You said: {m['label']}")


@bot.on_ticket
def work(t):                                    # t: a ticket - id, src, title, body
    bot.accept(t["id"])
    bot.finish(t["id"], "Echo", t["title"])


bot.run()
