// Example agent in Node.js (18+, no packages): watches RSS / Atom feeds and tells its lead about new items.
// It talks to the hub with plain fetch() - this file is also the reference for writing an agent in any language.
//
//   agents.yaml:  - {key: scout, name: Scout, role: Watches the news, reports_to: manager}
//   .env:         SCOUT_FEEDS=https://hnrss.org/frontpage,https://example.com/feed.xml   (SCOUT_EVERY=600 seconds)
//   run:          node examples/scout.mjs
//
// Protocol (AGENTS.md): POST /api/agents/<key>/sync with {status, cursor, ops[]}; every op carries an increasing
// number `n` so a resend after a network error is applied once. The response brings back chat messages, tickets
// and the team.
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
loadEnv(process.env.CREW_ENV_FILE || join(ROOT, ".env"));

const KEY = process.env.SCOUT_KEY || "scout";
const URL_ = (process.env.CREW_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const TOKEN = process.env[`AGENT_TOKEN_${KEY.toUpperCase()}`];
const FEEDS = (process.env.SCOUT_FEEDS || "https://hnrss.org/frontpage").split(",").map((s) => s.trim()).filter(Boolean);
const EVERY = Number(process.env.SCOUT_EVERY || 600) * 1000;
const PER_ROUND = 3;                                                 // new items passed on per feed per round, at most
const DATA = process.env.CREW_DATA_DIR || join(ROOT, "data");
const STATE_FILE = join(DATA, `agent-${KEY}.json`);

if (!TOKEN) { console.error(`no token: run \`python -m crew tokens\` (AGENT_TOKEN_${KEY.toUpperCase()})`); process.exit(1); }

let state = { cursor: 0, seen: {} };
try { state = { ...state, ...JSON.parse(readFileSync(STATE_FILE, "utf8")) }; } catch {}
const save = () => { mkdirSync(DATA, { recursive: true }); writeFileSync(STATE_FILE, JSON.stringify(state)); };

let seq = Date.now() * 1000;                                         // keeps increasing across restarts
const outbox = [];
const op = (o) => outbox.push({ n: ++seq, ...o });
let doing = "starting", team = [], lastRound = 0;

// ------------------------------------------------------------------------------------------- sync
async function sync() {
  const ops = outbox.slice(0, 200);
  let res;
  try {
    const r = await fetch(`${URL_}/api/agents/${KEY}/sync`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${TOKEN}` },
      body: JSON.stringify({ status: { doing, feeds: FEEDS.length }, cursor: state.cursor, ops }),
    });
    if (r.status === 401) { console.error("the hub rejected this agent's token"); process.exit(1); }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    res = await r.json();
  } catch (e) { console.warn(`sync failed (${e.message}); will retry`); return null; }
  outbox.splice(0, ops.length);
  for (const err of res.errors || []) console.warn(`op ${err.n} rejected: ${err.error}`);
  state.cursor = res.cursor; team = res.team || []; save();
  return res;
}

// ------------------------------------------------------------------------------------------- feeds
const decode = (s) => s.replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1").replace(/<[^>]+>/g, "")
  .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&").trim();
const tag = (xml, name) => { const m = xml.match(new RegExp(`<${name}[^>]*>([\\s\\S]*?)</${name}>`, "i")); return m ? decode(m[1]) : ""; };

function parse(xml) {
  const items = [...xml.matchAll(/<item[\s>][\s\S]*?<\/item>/gi), ...xml.matchAll(/<entry[\s>][\s\S]*?<\/entry>/gi)].map((m) => m[0]);
  return items.map((it) => ({
    title: tag(it, "title"),
    link: tag(it, "link") || (it.match(/<link[^>]*href="([^"]+)"/i) || [])[1] || "",
  })).filter((x) => x.title && x.link);
}

async function round() {
  const lead = (team.find((t) => t.key === KEY) || {}).reports_to;
  const to = lead && lead !== "you" ? lead : "you";
  for (const feed of FEEDS) {
    doing = `reading ${new URL(feed).hostname}`;
    let items = [];
    try {
      const r = await fetch(feed, { headers: { "User-Agent": "crew-scout" }, signal: AbortSignal.timeout(15000) });
      items = parse(await r.text());
      op({ op: "activity", to: "web", kind: "fetch", label: `${new URL(feed).hostname}: ${items.length} items`, meta: { url: feed } });
    } catch (e) {
      op({ op: "activity", to: "web", kind: "fetch", label: `${feed} failed: ${e.name}`, level: "warn" });
      continue;
    }
    const seen = new Set(state.seen[feed] || []);
    const fresh = items.filter((i) => !seen.has(i.link));
    if (state.seen[feed]) {                                          // first look at a feed: learn it quietly
      for (const it of fresh.slice(0, PER_ROUND)) op({ op: "say", to, text: `New: ${it.title}\n${it.link}` });
      if (fresh.length > PER_ROUND) op({ op: "say", to, text: `…and ${fresh.length - PER_ROUND} more on ${new URL(feed).hostname}.` });
    }
    state.seen[feed] = [...items.map((i) => i.link), ...seen].slice(0, 300);
  }
  doing = `watching ${FEEDS.length} feed${FEEDS.length === 1 ? "" : "s"}`;
  save();
}

// ------------------------------------------------------------------------------------------- loop
function onMessage(m) {
  op({ op: "say", to: m.src, text: `I watch ${FEEDS.length} feed(s): ${FEEDS.join(", ")}. New items go to my lead.` });
}
function onTicket(t) {
  op({ op: "decline", id: t.id, note: "I only watch feeds - add one to SCOUT_FEEDS and I'll pass on what's new." });
}

const handled = new Set();
async function step() {
  const res = await sync();
  if (!res) return;
  for (const m of res.messages || []) if (m.kind === "message") onMessage(m);
  for (const t of res.tickets || []) if (t.status === "open" && !handled.has(t.id)) { handled.add(t.id); onTicket(t); }
  if (Date.now() - lastRound > EVERY) { lastRound = Date.now(); await round(); }
  if (outbox.length) await sync();
}

console.log(`${KEY}: syncing with ${URL_}, ${FEEDS.length} feed(s) every ${EVERY / 1000}s`);
for (;;) { await step(); await new Promise((r) => setTimeout(r, 10000)); }

// ------------------------------------------------------------------------------------------- .env
function loadEnv(path) {
  if (!existsSync(path)) return;
  for (const line of readFileSync(path, "utf8").split("\n")) {
    const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
    if (m && !line.trim().startsWith("#") && process.env[m[1]] === undefined) process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
  }
}
