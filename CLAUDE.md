# Notes for AI assistants working in this repo

- This is a template: a hub (FastAPI + SQLite) for a team of AI agents, with a dashboard, an agent exchange and a
  visualizer feed. Read README.md, AGENTS.md (agent protocol) and DESIGN.md (design rules + visualizer contract).
- The team is data (`agents.yaml`). Never hardcode agent names or counts in code or pages.
- Agents talk to the hub only through `POST /api/agents/<key>/sync` (`crew/agents/sync.py`). Each agent's token opens
  only its own sync endpoint - keep it that way.
- Pages are single HTML files with inline scripts plus `web/static/`. No build step. The CSP allows same-origin
  assets only; add libraries under `web/static/` rather than from a CDN.
- Owner-only writes use the `owner` dependency in `crew/server.py`; reads use `auth`. A viewer must never be able to
  change anything.
- LLM calls go through `crew/agents/brain.py` (official Anthropic SDK, structured JSON output, daily budget).
- Run the tests after changes: `.venv/bin/python -m pytest -q`. Use `python -m crew demo` for visual work.
- Style only through the tokens in `web/static/base.css`; brand rules are in BRAND.md. Agents are coloured by slot
  (`Crew.agentColor`), status colours are reserved for state.
- Never commit `.env` or `data/`.
