# Design brief: make it yours

The template ships plain on purpose. The dashboard and exchange work, but look generic; the visualizer is a blank
scaffold. This file is the brief for designing and branding all three - written so you can hand it straight to an
AI assistant (Claude Code, etc.) along with your own direction.

## How to use this

1. Run `python -m crew demo 30` (or your real agents) so there is live data to look at.
2. Decide your direction - a name, a mood, references you like, colours, a metaphor for the visualizer.
3. Give your AI this file plus that direction, e.g.:

> Read DESIGN.md and AGENTS.md. Our brand is **<name>**: <mood / references / colours>. Restyle the dashboard and
> exchange to match (tokens in web/static/base.css, then the pages), and replace the placeholder scene in
> web/visualizer.html with **<your metaphor>**. Keep the data wiring, the API and every rule in DESIGN.md.

## Ground rules (keep these whatever the look)

- **Data comes only from the API.** No numbers or names baked into the pages; the team is whatever `agents.yaml` says
  (1 agent or 20).
- **Keep the wiring:** `/static/app.js` (login, `Crew.api`, polling), `/static/viz-data.js` (the visualizer feed).
  A page gets a 401 → it goes to the sign-in screen; a viewer must never see owner-only controls.
- **Self-contained by default.** The CSP allows scripts, styles, fonts and images from the hub itself only. Put
  fonts, images and libraries under `web/static/`. If you really need a CDN, set `CONTENT_SECURITY_POLICY` in `.env`
  and pin exact versions.
- **Every state is visible:** an agent is alive or offline (no sync for 3 minutes), busy (a pulse in the last minute),
  and may need attention (`approval` > `crit` > `issue`). The owner should spot "something needs me" in one second.
- **Works on a phone** (16px side gutters, no horizontal scroll), in light and dark (`prefers-color-scheme`), and with
  `prefers-reduced-motion` (pulses still show, without the motion).
- **Readable text**, keyboard-reachable controls, colour never the only signal (pair it with a shape or label).

## The visualizer data contract

`GET /api/viz?after=<cursor>` (owner or viewer session). `viz-data.js` polls it and gives you:

```js
const viz = VizData.start({ every: 2000 });
viz.on("update", (state) => draw(state));          // after every poll
viz.on("pulse", (pulse, state) => animate(pulse)); // each new signal, in order
```

**nodes** - `state.nodes` (a Map by id):

| field | meaning |
|---|---|
| `id` | agent key or line key (`you`, `llm`, `web`, ...) |
| `type` | `agent` or `line` |
| `label`, `role` | display name, one-line role |
| `parent` | who it reports to (`you` or another agent's id) - draw a hierarchy if you like |
| `alive` | synced in the last 3 minutes |
| `busy` | took part in a pulse in the last minute |
| `doing` | free text the agent reports ("reading ticket 12") |
| `heartbeat` | last sync, Unix ms (blink when it moves) |
| `attention` | `approval`, `crit`, `issue` or `null` |
| `slot` | 1-based position in agents.yaml (stable ordering for layouts) |
| *anything else* | extra fields from agents.yaml (`colour`, `icon`, `x`, `y`, ...) pass straight through |

**edges** - `state.edges`: `[{from, to, type: "reports_to"}]`.

**pulses** - `state.pulses` (last 500) and the `pulse` event:

| field | meaning |
|---|---|
| `from`, `to` | node ids |
| `kind` | `message`, `ticket`, `accept`, `decline`, `deliverable`, `note`, `issue`, `finding`, `approval`, `approve`, `reject`, `resolve`, `action`, `drop`, `llm`, or an agent's own (`search`, `fetch`, ...) |
| `label` | one line of text |
| `chat` | `true` = a message someone wrote; `false` = background activity (LLM calls, fetches) |
| `level` | `info` / `warn` / `crit` or null |
| `ref` | `report:<id>`, `ticket:<id>`, `drop:<id>` - link to the dashboard item |
| `meta` | whatever the agent attached (token counts, URLs, ...) |
| `t`, `id` | Unix ms, increasing id |

Also: `state.approvals` (how many items wait for the owner), `state.brand` (BRAND_NAME).

For replay / timelines: `GET /api/exchange/replay?hours=24` returns every signal in a window.

## Ideas for the visualizer metaphor

Pick one that fits your brand - or invent your own:

- an **office floor plan** - desks light up, paper planes carry messages
- a **subway / circuit map** - agents are stations, pulses are trains
- a **constellation** - agents are stars, the hierarchy is the constellation lines
- a **switchboard** - jacks and patch cords
- a **city at night** - windows light up as agents work
- a **mission control wall**, a **garden**, an **ant colony**, a **radio dial** ...

Good visualizers make three things readable at a glance: who is working, who is talking to whom, and whether anything
needs the owner.

## Branding checklist

- [ ] `BRAND_NAME` in `.env` (used in titles, login, alerts)
- [ ] Colour tokens and fonts in `web/static/base.css` (light + dark)
- [ ] `web/static/favicon.svg` and any logo / art under `web/static/`
- [ ] Sign-in screen (`web/index.html`, `#login`) - the first thing people see
- [ ] Dashboard and exchange restyled with the tokens
- [ ] Visualizer scene replaced (`web/visualizer.html`)
- [ ] Agent personalities: names, roles, prompts in `agents.yaml` and your agent files
