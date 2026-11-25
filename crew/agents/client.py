"""The agent side of the sync protocol (see sync.py), for agents written in Python. Any language works: an agent is
just a process that POSTs to /api/agents/<key>/sync with its token.

    from crew.agents.client import Agent

    bot = Agent("researcher")                        # url + token from CREW_URL and AGENT_TOKEN_RESEARCHER
    bot.on_message(lambda m: bot.say(m["src"], "Got it: " + m["label"]))
    bot.on_ticket(lambda t: bot.finish(t["id"], "Answer", "..."))
    bot.run(every=15)                                # sync, dispatch, repeat

Ops you queue (say, issue, ticket, ...) are sent on the next sync; if the hub is down they wait and go out later,
exactly once.
"""
import itertools
import json
import logging
import os
import time
from pathlib import Path

import httpx

from .. import config

log = logging.getLogger("crew.agent")
logging.getLogger("httpx").setLevel(logging.WARNING)


class Agent:
    def __init__(self, key, url=None, token=None, state_dir=None, timeout=30):
        self.key = key
        self.url = (url or config.env("CREW_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.token = token or os.environ.get(f"AGENT_TOKEN_{key.upper()}")
        if not self.token:
            raise SystemExit(f"no token for {key}: run `python -m crew tokens` (sets AGENT_TOKEN_{key.upper()} in .env)")
        self.http = httpx.Client(timeout=timeout, headers={"Authorization": f"Bearer {self.token}"})
        self.state_path = Path(state_dir or config.DATA_DIR) / f"agent-{key}.json"
        st = self._load()
        self.cursor = st.get("cursor", 0)
        self.done_actions = set(st.get("done_actions", []))
        self.outbox = []
        self._seq = itertools.count(int(time.time() * 1_000_000))
        self.doing, self.stats = "starting", {}
        self.team, self.tickets, self.approved = [], [], []
        self._handlers = {"message": [], "ticket": [], "approved": [], "rejected": [], "tick": []}
        self._seen_tickets = set()

    # ---------------------------------------------------------------------------------- state
    def _load(self):
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"cursor": self.cursor, "done_actions": sorted(self.done_actions)[-500:]}))

    # ---------------------------------------------------------------------------------- ops
    def _op(self, op, **fields):
        self.outbox.append({"n": next(self._seq), "op": op, **{k: v for k, v in fields.items() if v is not None}})

    def status(self, doing=None, **stats):
        if doing is not None:
            self.doing = doing
        self.stats.update(stats)

    def say(self, to, text, kind=None, ref=None):
        self._op("say", to=to, text=text, kind=kind, ref=ref)

    def activity(self, to, kind, label, text=None, meta=None, incoming=False):
        """A pulse for the visualizer that is not a chat message (an LLM call, a web fetch, a tool run...)."""
        self._op("activity", to=to, kind=kind, label=label, text=text, meta=meta, dir="in" if incoming else None, t=int(time.time() * 1000))

    def issue(self, key, title, body="", level="warn", facts=None, action=None):
        """Open (or refresh) an issue. action={"kind": ..., "label": ...} asks the owner to approve a step."""
        self._op("issue", key=key, level=level, title=title, body=body, facts=facts, action=action)

    def finding(self, key, title, body="", level="info", facts=None):
        self._op("finding", key=key, level=level, title=title, body=body, facts=facts)

    def resolve(self, key, resolution="cleared"):
        self._op("resolve", key=key, resolution=resolution)

    def note(self, title, body="", key="note", to=None):
        self._op("note", key=key, title=title, body=body, to=to)

    def deliver(self, title, body, key="deliverable", to=None, facts=None):
        self._op("deliver", key=key, title=title, body=body, to=to, facts=facts)

    def ticket(self, to, title, body=""):
        self._op("ticket", to=to, title=title, body=body)

    def accept(self, tid, note=None, priority=None):
        self._op("accept", id=tid, note=note, priority=priority)

    def decline(self, tid, note):
        self._op("decline", id=tid, note=note)

    def finish(self, tid, title, body):
        self._op("finish", id=tid, title=title, body=body)

    def action_done(self, rid, ok=True, note=None):
        self.done_actions.add(rid)
        self._op("action_done", id=rid, ok=ok, note=note)

    # ---------------------------------------------------------------------------------- brain
    def brain(self, system, **kw):
        """A Brain whose LLM calls show up on the exchange as pulses to the 'llm' line."""
        from .brain import Brain

        def hook(phase, info):
            if phase == "ask":
                self.activity("llm", "llm", f"asks {info['model']}")
            elif phase == "answer":
                self.activity("llm", "llm", f"{info['model']} answered", meta={"in": info["in"], "out": info["out"]}, incoming=True)
            else:
                self.activity("llm", "llm", f"LLM error: {info.get('error', '')[:120]}", incoming=True)
        return Brain(self.key, system, on_call=hook, **kw)

    # ---------------------------------------------------------------------------------- sync
    def sync(self):
        """Send queued ops + status, receive the inbox. -> the response dict, or None if the hub is unreachable."""
        sending = self.outbox[:200]
        body = {"status": {"doing": self.doing, **self.stats}, "cursor": self.cursor, "ops": sending}
        try:
            r = self.http.post(f"{self.url}/api/agents/{self.key}/sync", json=body)
            if r.status_code == 401:
                raise SystemExit(f"{self.key}: the hub rejected this agent's token (check AGENT_TOKEN_{self.key.upper()})")
            r.raise_for_status()
            res = r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("%s: sync failed (%s); will retry", self.key, e)
            return None
        del self.outbox[:len(sending)]
        for err in res.get("errors") or []:
            log.warning("%s: op %s rejected: %s", self.key, err.get("n"), err.get("error"))
        self.cursor = res.get("cursor", self.cursor)
        self.team, self.tickets, self.approved = res.get("team", []), res.get("tickets", []), res.get("approved", [])
        self._save()
        return res

    def flush(self):
        """Send queued ops now (from inside a handler, before slow work). Does not read the inbox - messages wait for
        the next sync, so nothing is skipped. -> True if sent."""
        if not self.outbox:
            return True
        sending = self.outbox[:200]
        body = {"status": {"doing": self.doing, **self.stats}, "cursor": self.cursor, "ops": sending}
        try:
            r = self.http.post(f"{self.url}/api/agents/{self.key}/sync", json=body)
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("%s: flush failed (%s); will retry", self.key, e)
            return False
        del self.outbox[:len(sending)]
        return True

    # ---------------------------------------------------------------------------------- handlers
    def on_message(self, fn):
        self._handlers["message"].append(fn); return fn

    def on_ticket(self, fn):
        """Called once per new open ticket on this agent's desk."""
        self._handlers["ticket"].append(fn); return fn

    def on_approved(self, fn):
        """Called once per approved action: carry it out, then call action_done(report['id'], ok, note)."""
        self._handlers["approved"].append(fn); return fn

    def on_tick(self, fn):
        """Called after every sync (periodic work: checks, summaries)."""
        self._handlers["tick"].append(fn); return fn

    def _fire(self, name, *args):
        for fn in self._handlers[name]:
            try:
                fn(*args)
            except Exception:
                log.exception("%s: %s handler failed", self.key, name)

    def step(self):
        """One sync + dispatch. Whatever the handlers queue is sent at once (and its replies dispatched), a few rounds at most."""
        res = self.sync()
        if res is None:
            return False
        self._dispatch(res)
        self._fire("tick", res)
        for _ in range(3):
            if not self.outbox:
                break
            res = self.sync()
            if res is None:
                break
            self._dispatch(res)
        return True

    def _dispatch(self, res):
        for m in res.get("messages") or []:
            self._fire("message", m)
        for t in self.tickets:
            if t["status"] == "open" and t["id"] not in self._seen_tickets:
                self._seen_tickets.add(t["id"])
                self._fire("ticket", t)
        for rep in self.approved:
            if rep["id"] not in self.done_actions:
                self.done_actions.add(rep["id"])
                self._fire("approved", rep)

    def run(self, every=15):
        log.info("%s: syncing with %s every %ss", self.key, self.url, every)
        self.status("on duty")
        while True:
            self.step()
            time.sleep(every)
