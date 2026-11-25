# Example agents

Each one is a complete agent you can run, read in a few minutes, and copy. Add it to `agents.yaml` (the entries are
there, commented out), run `python -m crew tokens`, then start it - on its own, or with
`AGENTS="manager researcher watchdog scout" ./run.sh`.

| File | Language | LLM | Shows |
|---|---|---|---|
| [`minimal.py`](minimal.py) | Python | no | The smallest agent: reply to messages, finish tickets. Start here. |
| [`manager.py`](manager.py) | Python | optional | Routing your messages and drops to teammates, relaying their answers, watching who goes quiet, an action that waits for your approval. |
| [`researcher.py`](researcher.py) | Python | optional | Working a ticket desk: accept / decline, answer with a structured Claude call. |
| [`watchdog.py`](watchdog.py) | Python | no | Real monitoring: URL checks as activity pulses, issues that open, escalate (warn → crit) and resolve themselves, a "mute for 24h" action you approve. |
| [`scout.mjs`](scout.mjs) | **Node.js** | no | The protocol from another language with zero packages: plain `fetch()` to the sync endpoint, RSS / Atom feeds, new items passed to its lead. Use it as the reference for Go, Rust, a shell script... |
| [`web_researcher.py`](web_researcher.py) | Python | **yes** | Claude researching on the live web (Roteo + Brave Search, or Claude's own search tool with an Anthropic key): every search shows up as a pulse to the Web door; answers come back with sources. |

## Settings

| Agent | `.env` |
|---|---|
| watchdog | `WATCH_URLS` (comma separated), `WATCH_EVERY` (s, default 60), `WATCH_TIMEOUT` (s) |
| scout | `SCOUT_FEEDS` (comma separated), `SCOUT_EVERY` (s, default 600) |
| web_researcher | `ROTEO_API_KEY` + `BRAVE_API_KEY` (or `ANTHROPIC_API_KEY`), `WEBRESEARCH_SEARCHES` (per ticket, default 5), `WEBRESEARCH_PER_DAY` (default 30) |
| manager, researcher | `ROTEO_API_KEY` optional - without it they answer with simple rules |

Each agent's key defaults to its file name's role (`watchdog`, `scout`, `webresearch`); run a second copy under
another key with `WATCHDOG_KEY=...`, `SCOUT_KEY=...` or `WEBRESEARCH_KEY=...`.

## Patterns worth copying

- **Learn quietly first** (scout): on the first look at a source, remember what is there without announcing it -
  otherwise every restart floods the chat.
- **Fail twice before you shout** (watchdog): one failed check is noise; two in a row opens an issue, five makes it
  critical. Resolve it yourself when the cause clears, saying how long it lasted.
- **Ask, then act** (watchdog, manager): anything with side effects goes in `action=` and waits for the owner's
  Approve; carry it out in `on_approved` and report with `action_done`.
- **Show the work** (watchdog, scout, web_researcher): `activity(...)` pulses make a visualizer come alive without
  cluttering the chat.
- **Budget everything that costs money** (web_researcher, brain): a daily cap, and a polite decline when it is spent.
- **Flush before slow work** (web_researcher): `bot.flush()` sends "accepted" right away, before a minute-long task.
