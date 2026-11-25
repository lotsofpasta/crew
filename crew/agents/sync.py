"""The sync protocol: the one way an agent talks to the hub. POST /api/agents/<key>/sync with its own bearer token.

Request body (every field optional):
  {
    "status":  {"doing": "reading ticket 12", ...any small numbers for its card},
    "cursor":  <the last inbox id it has seen; 0 on first start>,
    "ops":     [ {"n": <increasing int>, "op": "<name>", ...fields}, ... ]
  }
Each op carries the agent's own sequence number `n`. The hub remembers the highest one it applied, so an op resent
after a network error is skipped (exactly-once). Use a number that keeps increasing across restarts (the client
uses time in microseconds).

ops
  say          {to, text, kind?}                  a chat message to a teammate, a line, or 'you'
  activity     {to, kind, label, text?, meta?, dir?}  a non-chat pulse for the visualizer (dir 'in' = from `to`)
  issue        {key, level, title, body?, facts?, action?}   open / refresh an issue (action -> approval request)
  finding      {key, level, title, body?, facts?}  an observation with the same lifecycle
  resolve      {key, resolution}                  clear its open issue / finding with that key
  note         {key, title, body, to?}            a status update
  deliver      {key, title, body, to?, facts?}    finished work
  ticket       {to, title, body?}                 ask a teammate for something
  accept       {id, note?, priority?}             take a ticket from its desk
  decline      {id, note}                         turn one down, saying why
  finish       {id, title, body}                  hand the result back to whoever asked
  action_done  {id, ok, note?}                    report on an approved action (id = the report id)

Response:
  {
    "cursor":   <new cursor>,
    "messages": [chat signals addressed to it after the cursor: from 'you', teammates, ticket replies, drops],
    "tickets":  [its open / accepted tickets],
    "approved": [its reports whose action the owner approved - carry them out, then send action_done],
    "rejected": [ids of its proposals the owner rejected since the cursor],
    "team":     [{key, name, role, reports_to, alive, doing}],
    "applied":  <how many ops were applied>, "errors": [{n, error}]
  }
"""
from .. import db
from . import registry, signals, store, tickets

MAX_OPS = 200


def _str(v, n):
    return str(v or "")[:n]


def apply_op(conn, agent, op, now):
    kind = op.get("op")
    ends = registry.endpoints()
    if kind == "say":
        to = op.get("to")
        if to not in ends or to == agent:
            raise ValueError(f"unknown recipient {to!r}")
        text = _str(op.get("text"), 4000).strip()
        if not text:
            raise ValueError("empty message")
        label, body = (text, None) if len(text) <= 300 else (text[:297] + "…", text)
        signals.emit(conn, agent, to, _str(op.get("kind") or "message", 24), label, body, ref=op.get("ref"), chat=True, t=now,
                     commit=False)
    elif kind == "activity":
        to = op.get("to") if op.get("to") in ends else "llm"
        src, dst = (to, agent) if op.get("dir") == "in" else (agent, to)
        t = min(int(op.get("t") or now), now)
        signals.emit(conn, src, dst, _str(op.get("kind") or "activity", 24), _str(op.get("label"), 300), op.get("text"),
                     level=op.get("level"), ref=op.get("ref"), chat=False, t=t, meta=op.get("meta"), commit=False)
    elif kind in ("issue", "finding"):
        if not op.get("key") or not op.get("title"):
            raise ValueError("issue needs key and title")
        action = op.get("action") if isinstance(op.get("action"), dict) and kind == "issue" else None
        if action and not action.get("label"):
            action["label"] = _str(action.get("kind") or "action", 80)
        store.upsert(conn, agent, _str(op["key"], 120), op.get("level") or "warn", _str(op["title"], 200), _str(op.get("body"), 4000),
                     facts=op.get("facts"), action=action, now=now, kind=kind)
    elif kind == "resolve":
        store.resolve_key(conn, agent, _str(op.get("key"), 120), _str(op.get("resolution") or "cleared", 500), now)
    elif kind in ("note", "deliver"):
        to = op.get("to") if op.get("to") in ends else "you"
        fn = store.note if kind == "note" else store.deliver
        fn(conn, agent, _str(op.get("key") or kind, 120), _str(op.get("title"), 200), _str(op.get("body"), 4000),
           facts=op.get("facts"), now=now, to=to)
    elif kind == "ticket":
        if op.get("to") not in registry.keys():
            raise ValueError(f"no agent {op.get('to')!r} to file a ticket with")
        t, _ = tickets.file(conn, agent, op["to"], op.get("title") or tickets.title_of(op.get("body")), op.get("body") or "", now=now)
        if t is None:
            raise ValueError("ticket not filed (empty, or the daily cap is reached)")
    elif kind in ("accept", "decline"):
        if not tickets.update(conn, int(op.get("id") or 0), "accepted" if kind == "accept" else "declined", op.get("note"),
                              op.get("priority"), now=now, by=agent):
            raise ValueError("not your open ticket")
    elif kind == "finish":
        if not tickets.finish(conn, int(op.get("id") or 0), op.get("title") or "Done", op.get("body") or "", now=now, by=agent):
            raise ValueError("not your open ticket")
    elif kind == "action_done":
        rep = store.get(conn, int(op.get("id") or 0))
        if not rep or rep["agent"] != agent or rep["action_state"] != "approved":
            raise ValueError("no approved action with that id")
        ok = bool(op.get("ok"))
        store.set_action(conn, rep["id"], "done" if ok else "failed", _str(op.get("note"), 500), now)
        label = (rep["action"] or {}).get("label", "action")
        signals.emit(conn, agent, "you", "action", f"{'Done' if ok else 'Failed'}: {label}", op.get("note"),
                     level=None if ok else "warn", ref=f"report:{rep['id']}", chat=True, t=now, commit=False)
        if ok:
            store.resolve(conn, rep["id"], op.get("note") or f"{label}: done", now)
    else:
        raise ValueError(f"unknown op {kind!r}")


def handle(conn, agent, body, now=None):
    now = now or db.now_ms()
    store.init(conn)
    tickets.init(conn)
    body = body if isinstance(body, dict) else {}
    st = body.get("status") if isinstance(body.get("status"), dict) else {}
    store.checkin(conn, agent, st.get("doing"), now, **{k: v for k, v in st.items() if k != "doing" and isinstance(v, (int, float, str, bool))})

    seq_key = f"sync_seq:{agent}"
    last = db.kv_get(conn, seq_key) or 0
    top, applied, errors = last, 0, []
    ops = [o for o in (body.get("ops") or [])[:MAX_OPS] if isinstance(o, dict)]
    for op in sorted(ops, key=lambda o: o.get("n") or 0):
        n = int(op.get("n") or 0)
        if n <= last:
            continue
        try:
            apply_op(conn, agent, op, now)
            applied += 1
        except (ValueError, TypeError, KeyError) as e:
            errors.append({"n": n, "error": str(e)})
        top = max(top, n)
    conn.commit()
    if top != last:
        db.kv_set(conn, seq_key, top)

    cursor = int(body.get("cursor") or 0)
    msgs = signals.inbox(conn, agent, cursor)
    new_cursor = msgs[-1]["id"] if msgs else max(cursor, 0)
    rejected = [int(m["ref"].split(":")[1]) for m in msgs if m["kind"] == "reject" and (m.get("ref") or "").startswith("report:")]
    team = [{"key": a["key"], "name": a["name"], "role": a["role"], "reports_to": a["reports_to"], "alive": a["alive"],
             "doing": a.get("doing")} for a in registry.roster(conn, now)]
    return {"cursor": new_cursor, "messages": msgs, "tickets": tickets.open_for(conn, agent),
            "approved": store.approved_for(conn, agent), "rejected": rejected, "team": team,
            "applied": applied, "errors": errors, "t": now}
