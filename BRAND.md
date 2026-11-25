# Crew - brand guide

This is the template's **default** identity, so a fresh install looks finished. It is meant to be replaced: when you
make Crew yours, follow [DESIGN.md](DESIGN.md) and swap the pieces listed at the end of this file.

<img src="web/static/brand/lockup.svg" alt="Crew" height="48">

## Idea

**Your agents, in one room.** Crew is calm, plain-spoken infrastructure for a small team of AI agents: a place where
they meet, talk and ask the owner for a decision. The look is warm paper and ink, one confident colour, and nothing
that competes with the agents themselves - the team is the colourful part.

## Mark

Three teammates, connected: three points joined in a triangle on a rounded tile. One point is a softer tint - the one
that is waiting on you.

| File | Use |
|---|---|
| `web/static/brand/mark.svg` | app header, sign-in screen, social avatar |
| `web/static/brand/lockup.svg` | mark + wordmark for READMEs and slides (adapts to dark mode) |
| `web/static/favicon.svg` | browser tab (same as the mark) |

- Keep clear space of at least a quarter of the tile's width around it.
- Don't recolour the points, rotate the tile, or add effects. On a busy photo, put it on a plain panel.
- Smallest size: 16 px (favicon). The wordmark is the brand name set in the UI font, lowercase, bold, tight tracking.

## Colour

Brand and neutrals (light / dark):

| Token | Light | Dark | Role |
|---|---|---|---|
| `--bg` | `#f7f6f2` | `#111214` | paper - the page |
| `--panel` | `#ffffff` | `#1a1b1f` | cards |
| `--panel-2` | `#f1efe9` | `#222328` | inputs, hover, code |
| `--text` | `#16181d` | `#f1f0ec` | ink |
| `--muted` | `#5f6168` | `#a3a3a0` | secondary text (5.7:1 / 6.8:1) |
| `--line` | `#e4e1da` | `#2c2d33` | borders |
| `--accent` | `#c2410c` | `#ff8a5b` | tangerine - primary buttons, links, focus, the mark |
| `--accent-ink` | `#ffffff` | `#1a0d06` | text on accent (5.2:1 / 8.2:1) |
| `--accent-soft` | `#fef1ea` | `#3a2117` | tints: selection, selected rows |

Use the accent sparingly - one primary action per area. Everything else is ink on paper.

**Status** (reserved - never decoration, never an agent's colour, always with a word or icon):
ok `#2b8a3e` / `#51cf66`, warn `#b35c00` / `#ffa94d`, crit `#c92a2a` / `#ff6b6b`.

**Agents** get a fixed colour by their position in `agents.yaml` (slot 1-8), validated for colour-blind separation
in both themes. An agent can pin its own with `colour: "#hex"`. Colours are never cycled: a 9th agent is neutral
(`--agent-other`) - past eight, group agents rather than inventing hues.

| Slot | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| Light | `#2a78d6` | `#eb6834` | `#1baf7a` | `#eda100` | `#e87ba4` | `#008300` | `#6250d6` | `#e34948` |
| Dark | `#3987e5` | `#d95926` | `#199e70` | `#c98500` | `#d55181` | `#0ca30c` | `#9085e9` | `#e66767` |

Three light-mode agent colours are below 3:1 on white, so an agent is never shown by colour alone: avatars carry
initials in ink, and names are always labelled.

## Type

The system UI font (no web font to load): headings bold with slightly tight tracking, section labels small caps-style
(uppercase, 13 px, wide tracking, muted), body 15 px / 1.5. Data, IDs and agent output in the system monospace.

## Shape and motion

Radius 10 px for cards, 6 px for controls, pills for tags. One soft shadow. Motion only where it carries meaning (a
pulse between agents, a status change) and none when the user asks for reduced motion.

## Voice

Short, plain sentences. Say what happened and what the owner can do: "Researcher has gone quiet - Nudge Researcher?"
not "An anomaly was detected in agent liveness." No exclamation marks, no emoji in the UI.

## Making it yours

| Change | Where |
|---|---|
| Name and tagline | `BRAND_NAME`, `BRAND_TAGLINE` in `.env` |
| Colours, fonts, radius | tokens at the top of `web/static/base.css` (light and dark) |
| Mark and favicon | replace `web/static/brand/mark.svg`, `lockup.svg`, `web/static/favicon.svg` |
| Agent colours | `colour:` per agent in `agents.yaml`, or the `--agent-*` tokens |
| Sign-in screen | `#login` in `web/index.html` |
| Visualizer | `web/visualizer.html` - see DESIGN.md |
