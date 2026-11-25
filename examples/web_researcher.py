"""Example agent: researches tickets on the live web with Claude, and cites its sources.
Run: python examples/web_researcher.py

  agents.yaml:  - {key: webresearch, name: Web researcher, role: Looks things up online, reports_to: manager}
  .env:         WEBRESEARCH_SEARCHES=5   (web searches per ticket, at most)  WEBRESEARCH_PER_DAY=30 (tickets a day)

Two ways to search, picked by the LLM key in .env:
  ROTEO_API_KEY + BRAVE_API_KEY  the agent runs the searches itself: Claude asks for `web_search` / `read_page`
                                 (function calls through the gateway), the agent runs them on the Brave Search API
                                 (https://brave.com/search/api) and plain HTTP, and hands back the results
  ANTHROPIC_API_KEY              Claude's own server-side web search tool; no search key needed

Every search appears on the exchange as a 'search' pulse to the 'web' line (and every Claude call as an 'llm'
pulse), so a visualizer shows the work as it happens. The answer goes back to whoever filed the ticket with the
sources listed underneath.
"""
import html
import json
import logging
import os
import re
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
BRAVE_KEY = os.environ.get("BRAVE_API_KEY")

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


def research_anthropic(task):
    """-> (answer text, [(title, url)]). Runs Claude's server-side web search tool; resumes paused turns."""
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


# --- Roteo (or any OpenAI-style gateway): the agent runs the tools itself -----------------------------------------
TOOLS = [
    {"type": "function", "function": {"name": "web_search", "description": "Search the web. Returns titles, URLs and snippets.",
     "parameters": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "read_page", "description": "Read the text of one web page (first ~6,000 characters).",
     "parameters": {"type": "object", "required": ["url"], "properties": {"url": {"type": "string"}}}}},
]


def brave(query):
    import httpx
    r = httpx.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": 6},
                  headers={"X-Subscription-Token": BRAVE_KEY, "Accept": "application/json"}, timeout=20)
    r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": re.sub(r"<[^>]+>", "", x.get("description", ""))}
            for x in (r.json().get("web") or {}).get("results", [])]


def read_page(url):
    import httpx
    r = httpx.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": "crew-webresearch"})
    text = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", r.text)
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))).strip()
    return text[:6000]


def research_roteo(task):
    """-> (answer text, [(title, url)]). A function-calling loop: Claude asks, the agent searches, Claude answers."""
    messages = [{"role": "system", "content": SYSTEM + f"\nYou have at most {MAX_SEARCHES} web_search calls; use read_page"
                 " to check a promising source. Write the final answer as plain text."},
                {"role": "user", "content": task}]
    sources, searches = {}, 0
    for _ in range(MAX_SEARCHES + 6):
        bot.activity("llm", "llm", f"asks {MODEL} (with web search)")
        j = brainmod.roteo_chat(messages, MODEL, tools=TOOLS)
        u = j.get("usage") or {}
        bot.activity("llm", "llm", f"{j.get('model') or MODEL} answered", incoming=True,
                     meta={"in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0)})
        msg = (j.get("choices") or [{}])[0].get("message") or {}
        calls = msg.get("tool_calls") or []
        if not calls:
            return (msg.get("content") or "").strip(), list(sources.items())
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            name = c["function"]["name"]
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            try:
                if name == "web_search" and searches >= MAX_SEARCHES:
                    out = "Search limit reached - answer with what you have."
                elif name == "web_search":
                    searches += 1
                    q = str(args.get("query", ""))[:200]
                    bot.activity("web", "search", f"search: {q[:120]}")
                    hits = brave(q)
                    for h in hits:
                        sources.setdefault(h["url"], h["title"])
                    bot.activity("web", "search", f"{len(hits)} results", incoming=True)
                    out = json.dumps(hits)
                elif name == "read_page":
                    url = str(args.get("url", ""))
                    bot.activity("web", "fetch", f"read: {url[:120]}")
                    out = read_page(url)
                    sources.setdefault(url, "")
                else:
                    out = f"unknown tool {name}"
            except Exception as e:                             # a failed search is information for the model, not a crash
                bot.activity("web", "search", f"{name} failed: {type(e).__name__}", incoming=True)
                out = f"{name} failed: {type(e).__name__}"
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": out})
    raise RuntimeError("no answer after the search limit")


def research(task):
    return research_roteo(task) if brainmod.provider() == "roteo" else research_anthropic(task)


@bot.on_ticket
def work(t):
    if not brainmod.available():
        bot.decline(t["id"], "I need an LLM key to search the web: ROTEO_API_KEY (with BRAVE_API_KEY) or ANTHROPIC_API_KEY.")
        return
    if brainmod.provider() == "roteo" and not BRAVE_KEY:
        bot.decline(t["id"], "I need BRAVE_API_KEY to search the web through Roteo (free tier: https://brave.com/search/api).")
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
