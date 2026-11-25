"""Example agent: researches tickets on the live web with Claude's web search tool, and cites its sources.
Needs ANTHROPIC_API_KEY. Run: python examples/web_researcher.py

  agents.yaml:  - {key: webresearch, name: Web researcher, role: Looks things up online, reports_to: manager}
  .env:         WEBRESEARCH_SEARCHES=5   (web searches per ticket, at most)  WEBRESEARCH_PER_DAY=30 (tickets a day)

Every search it runs appears on the exchange as a 'search' pulse to the 'web' line (and every Claude call as an
'llm' pulse), so a visualizer shows the work as it happens. The answer goes back to whoever filed the ticket with
the sources listed underneath.
"""
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from crew import clock, config  # noqa: E402
from crew.agents import brain as brainmod  # noqa: E402
from crew.agents.client import Agent  # noqa: E402

KEY = os.environ.get("WEBRESEARCH_KEY", "webresearch")
MODEL = config.env("CREW_MODEL", "claude-opus-5-5")
MAX_SEARCHES = int(os.environ.get("WEBRESEARCH_SEARCHES", "5"))
PER_DAY = int(os.environ.get("WEBRESEARCH_PER_DAY", "30"))
SYSTEM = """You are the team's web researcher. Answer the task using web search; prefer primary sources. Be concise:
a direct answer first, then the key supporting points. Say plainly when sources disagree or you could not confirm
something. Do not invent sources."""

log = logging.getLogger("webresearch")
bot = Agent(KEY)
budget_path = Path(config.DATA_DIR) / f"budget-{KEY}.json"


def spent_today():
    try:
        b = json.loads(budget_path.read_text())
    except (OSError, ValueError):
        b = {}
    return b.get("n", 0) if b.get("day") == clock.local_day() else 0


def count():
    budget_path.parent.mkdir(parents=True, exist_ok=True)
    budget_path.write_text(json.dumps({"day": clock.local_day(), "n": spent_today() + 1}))


def research(task):
    """-> (answer text, [(title, url)]). Runs the server-side web search tool; resumes paused turns."""
    import anthropic
    client = anthropic.Anthropic(timeout=600.0, max_retries=2)
    messages = [{"role": "user", "content": task}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": MAX_SEARCHES}]
    sources, text = {}, []
    for _ in range(4):                                         # a long search turn may pause; send it back to continue
        bot.activity("llm", "llm", f"asks {MODEL} (with web search)")
        resp = client.messages.create(model=MODEL, max_tokens=16000, system=SYSTEM, tools=tools, messages=messages,
                                      output_config={"effort": "medium"})
        bot.activity("llm", "llm", f"{resp.model} answered", incoming=True,
                     meta={"in": resp.usage.input_tokens, "out": resp.usage.output_tokens})
        if resp.stop_reason == "refusal":
            raise RuntimeError("the model declined this task")
        for b in resp.content:
            if b.type == "server_tool_use" and b.name == "web_search":
                bot.activity("web", "search", f"search: {str((b.input or {}).get('query', ''))[:120]}")
            elif b.type == "web_search_tool_result":
                if isinstance(b.content, list):                # a list = results; an object = an error (no exception)
                    for r in b.content:
                        sources.setdefault(r.url, r.title)
                    bot.activity("web", "search", f"{len(b.content)} results", incoming=True)
                else:
                    bot.activity("web", "search", f"search error: {getattr(b.content, 'error_code', '?')}", incoming=True)
            elif b.type == "text":
                text.append(b.text)
        if resp.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": resp.content})
    return "".join(text).strip(), list(sources.items())


@bot.on_ticket
def work(t):
    if not brainmod.available():
        bot.decline(t["id"], "I need ANTHROPIC_API_KEY to search the web.")
        return
    if spent_today() >= PER_DAY:
        bot.decline(t["id"], f"I've done my {PER_DAY} searches for today - try again tomorrow.")
        return
    bot.accept(t["id"], "Searching the web for this.")
    bot.status(f"researching #{t['id']}")
    bot.flush()                                                # show 'accepted' now; research can take a minute
    task = t["title"] + ("\n\n" + t["body"] if t.get("body") else "")
    try:
        count()
        answer, sources = research(task)
    except Exception as e:
        log.exception("research failed")
        bot.finish(t["id"], "Couldn't finish this", f"The search failed ({type(e).__name__}). Try again later.")
        return
    refs = "\n".join(f"- {title or url}: {url}" for url, title in sources[:8])
    bot.finish(t["id"], t["title"][:120], answer + (f"\n\nSources:\n{refs}" if refs else ""))
    bot.status("on duty")


@bot.on_message
def chat(m):
    if m["kind"] == "message":
        bot.say(m["src"], "File a ticket with your question and I'll look it up on the web.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    bot.run()
