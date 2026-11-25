# Writing an agent

An agent is any process that calls `POST /api/agents/<key>/sync` every few seconds with its own token. That's the
whole contract - Python, Node, Go, a shell script, a Claude Agent SDK app: all fine.

## 1. Add it to the team

```yaml
# agents.yaml
agents:
  - key: scout              # lowercase, used in URLs and @mentions
    name: Scout
    role: Watches the news
    reports_to: manager     # or "you"
    model: claude-opus-5-5
    about: Reads a few feeds and flags what matters.
    colour: "#e8590c"       # any extra field is passed through to the dashboard and /api/viz
```

```sh
python -m crew tokens     # adds AGENT_TOKEN_SCOUT to .env
```

## 2. Write it (Python)

```python
from crew.agents.client import Agent

bot = Agent("scout")                         # CREW_URL + AGENT_TOKEN_SCOUT from the environment / .env
brain = bot.brain("You are Scout, ...")      # Claude calls that show up on the exchange as pulses to 'llm'

@bot.on_message                              # chat messages addressed to it (from you or teammates)
def on_message(m):
    bot.say(m["src"], "On it.")

@bot.on_ticket                               # a new ticket on its desk
def on_ticket(t):
    bot.accept(t["id"])
    bot.finish(t["id"], "Result", "...")     # goes back to whoever asked

@bot.on_approved                             # the owner approved an action it proposed
def on_approved(report):
    ok = do_the_thing(report["action"])
    bot.action_done(report["id"], ok, "what happened")

@bot.on_tick                                 # after every sync: periodic checks
def tick(_):
    if something_is_wrong():
        bot.issue("feed-down", "The feed is down", "details...", level="warn",
                  action={"kind": "restart_feed", "label": "Restart the feed reader"})
    else:
        bot.resolve("feed-down", "Feed is back.")

bot.run(every=15)
```

Everything you queue (`say`, `issue`, `ticket`, `deliver`, ...) is sent on the next sync and applied exactly once,
even if the hub was down for a while. `bot.activity("web", "fetch", "Read example.com")` puts a non-chat pulse on the
exchange for the visualizer.

The brain: `brain.ask(prompt, json_schema)` returns parsed JSON; `light=True` uses the cheaper model; a daily call
budget stops a runaway loop (`daily_calls=`); `check_numbers=lambda r: [r["summary"]]` rejects an answer that
contains a number that was not in the prompt (compute numbers in code, let the model explain them).

Everything you queue goes out on the next sync. Before a slow task, `bot.flush()` sends what is queued right away
(e.g. "accepted") without reading the inbox early.

Text is shown as markdown on the dashboard and the exchange: `**bold**`, `*italic*`, `` `code` ``, `-` and `1.` lists,
`#` headings, `>` quotes, fenced code blocks and http(s) links. Raw HTML is shown as text, never run.

More complete examples - a URL watchdog, a Node.js feed reader, a web-search researcher - are in
[examples/README.md](examples/README.md).

## 3. Run it

- Locally: `python agents/scout.py` (or add it to `AGENTS="manager researcher scout" ./run.sh`).
- systemd: put it in `agents/scout.py` and `systemctl enable --now crew-agent@scout` (see `deploy/`).
- Elsewhere: the agent's machine needs only `CREW_URL=https://your-hub` and `AGENT_TOKEN_SCOUT`.

## The sync protocol (for other languages)

A complete agent in Node.js with no packages is in [examples/scout.mjs](examples/scout.mjs) - use it as the reference.

`POST /api/agents/<key>/sync`, header `Authorization: Bearer <AGENT_TOKEN_KEY>`, JSON body:

```json
{
  "status": {"doing": "reading feeds", "items_today": 12},
  "cursor": 0,
  "ops": [
    {"n": 1732500000000001, "op": "say", "to": "manager", "text": "Found something"},
    {"n": 1732500000000002, "op": "issue", "key": "feed-down", "level": "warn", "title": "Feed down",
     "action": {"kind": "restart_feed", "label": "Restart the feed reader"}}
  ]
}
```

`n` must keep increasing across restarts (time in microseconds works); ops at or below the last applied `n` are
skipped, so resending after a network error is safe. Keep `cursor` from each response and send it back next time.

| op | fields | does |
|---|---|---|
| `say` | `to, text, kind?` | chat message to a teammate, a line, or `you` |
| `activity` | `to, kind, label, text?, meta?, dir?` | non-chat pulse (`dir: "in"` = from `to` back to the agent) |
| `issue` | `key, level, title, body?, facts?, action?` | open / refresh an issue; `action` = approval request |
| `finding` | `key, level, title, body?, facts?` | an observation with the same lifecycle |
| `resolve` | `key, resolution` | clear its open issue / finding |
| `note` | `key, title, body, to?` | status update |
| `deliver` | `key, title, body, to?, facts?` | finished work |
| `ticket` | `to, title, body?` | ask a teammate (12 a day max per agent) |
| `accept` / `decline` | `id, note?` | answer a ticket on its desk |
| `finish` | `id, title, body` | hand a ticket's result back |
| `action_done` | `id, ok, note?` | report on an approved action |

Response: `{cursor, messages[], tickets[], approved[], rejected[], team[], applied, errors[]}` - `messages` are chat
signals addressed to the agent after the cursor (from you, teammates, ticket replies, drops); `approved` are reports
whose action the owner approved and that still wait for `action_done`.

Levels: `info`, `warn`, `crit`. A level going up re-announces the issue (and `crit` / approvals go to Telegram if
configured).
