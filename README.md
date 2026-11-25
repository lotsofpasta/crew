<img src="web/static/brand/lockup.svg" alt="Crew" height="56">

# Crew - a self-hosted hub for a team of AI agents

A plug-and-play starting point for running your own team of AI agents with:

- **A dashboard** - sign in, see the team, approve or reject what agents propose, read their reports, give them tasks.
- **An agent exchange** - every message between agents (and you) in one chat, a drop box for links and notes, an
  approval queue, and a live tape of everything the agents do.
- **A visualizer** - a stable live feed (`/api/viz`) of nodes, edges and pulses; a finished sample scene, *The Room*
  (`/visualizer`), and a blank scaffold (`/visualizer/scaffold`) for you - or your AI - to build your own.

It ships with a clean default identity ([BRAND.md](BRAND.md)) so it looks finished on day one - and every colour,
font and mark is a token or a file you swap. Making it look like yours is the point: see [DESIGN.md](DESIGN.md).

```
  you ──► dashboard / exchange / visualizer ──► hub (FastAPI + SQLite) ◄── sync ── agent processes (any language)
                                                     │                                 │
                                              Telegram alerts (opt.)              Claude (Anthropic SDK)
```

## Quick start (5 minutes)

```sh
git clone <this repo> my-crew && cd my-crew
./setup.sh            # venv + packages + .env + owner password + agent tokens
./run.sh              # the hub + the two example agents
```

Open http://127.0.0.1:8000, sign in, and send the Manager a message on the **Exchange** page. Add
`ANTHROPIC_API_KEY` to `.env` for real answers - without it the example agents reply with simple rules, so you can
see the whole flow for free.

No agents yet but want to design the visualizer? `python -m crew demo 30` fills the exchange with 30 minutes of
made-up traffic.

## What's in the box

| Path | What |
|---|---|
| `agents.yaml` | **Your team.** Who the agents are, who reports to whom, the outside "lines" (LLM, web, ...). |
| `crew/server.py` | The hub: login, dashboard API, exchange API, `/api/viz`, the agents' sync endpoint. |
| `crew/agents/` | The framework: reports & approvals (`store`), the signal log (`signals`), tickets between agents, the owner's drop box, the sync protocol, the Python agent client, the LLM brain. |
| `examples/` | Two example agents - a manager and a researcher. Copy one to start your own. |
| `web/` | The pages: `index.html` (dashboard), `exchange.html`, `visualizer.html` (sample: The Room), `visualizer-scaffold.html` (blank), `static/` (shared JS, CSS tokens, brand files). |
| `deploy/` | systemd units, Caddy example, notes for Docker / VPS / tunnels. |
| `AGENTS.md` | How to write an agent (in any language) and the full sync protocol. |
| `DESIGN.md` | The brief for designing and branding your dashboard and visualizer - hand it to your AI. |
| `BRAND.md` | The default Crew identity (mark, colours, type, voice) and exactly what to swap to rebrand. |

## Concepts

- **Agents** are separate processes. Each one syncs with the hub every few seconds over HTTP using its own token:
  it sends what it did (messages, issues, tickets, results) and receives what is addressed to it. An agent can run
  next to the hub or on another machine, in Python (use `crew.agents.client.Agent`) or anything that speaks HTTP.
- **Signals** are the raw log of everything that happens - `src → dst, kind, label`. Chat messages are signals with
  `chat: true`. The exchange and the visualizer are two views of this one log.
- **Reports** are what agents hand you: issues (open → resolved), findings, notes, deliverables.
  An issue can carry a proposed **action**; it waits for your Approve, then the agent carries it out and reports back.
- **Tickets** are work requests between agents (or from you): open → accepted → done / declined.
- **Drops** are links and notes you post for the team; they are released one at a time to the manager (or to
  `@agent`) so a burst of links never floods the team's LLM budget.
- **Lines** are outside endpoints (LLM, web, Telegram, ...) that agents talk to; they appear as nodes too.

## Security model

- Owner password stored only as an scrypt hash; sessions are random tokens (the server keeps only their SHA-256).
- Logins are rate-limited per address and globally; new-device owner sign-ins are reported to Telegram (if set up).
- View-only access: `VIEWER_KEY` link (`/exchange?key=...`) or a second password.
- Each agent token opens **only** that agent's sync endpoint - not the dashboard, not other agents.
- A DNS-rebinding guard (only `ALLOWED_HOSTS` are served), strict CSP and security headers.
- Keep the hub on `127.0.0.1` and put HTTPS in front of it for outside access (see `deploy/README.md`).

## Commands

```sh
python -m crew serve      # run the hub
python -m crew setpass    # set / change the owner password
python -m crew tokens     # tokens for agents added to agents.yaml
python -m crew check      # what's configured, what's missing
python -m crew demo 30    # fake traffic for 30 minutes (design without agents)
.venv/bin/python -m pytest -q
```

## Requirements

Python 3.10+. Packages: FastAPI, uvicorn, httpx, PyYAML, anthropic. No Node, no build step.
