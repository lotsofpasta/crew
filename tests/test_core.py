import pytest

from crew import auth, db
from crew.agents import brain, drops, signals, store, tickets


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    store.init(c); tickets.init(c); drops.init(c)
    return c


def test_issue_lifecycle(conn):
    r, ch = store.upsert(conn, "manager", "k1", "warn", "Something")
    assert ch == "new" and r["status"] == "open"
    r2, ch2 = store.upsert(conn, "manager", "k1", "warn", "Something again")
    assert r2["id"] == r["id"] and ch2 is None
    _, ch3 = store.upsert(conn, "manager", "k1", "crit", "Worse")
    assert ch3 == "up"
    assert store.resolve_key(conn, "manager", "k1", "fixed")
    assert store.get(conn, r["id"])["status"] == "resolved"
    kinds = [s["kind"] for s in signals.recent(conn)]
    assert kinds.count("issue") == 2 and "resolve" in kinds


def test_approval_request(conn):
    r, _ = store.upsert(conn, "manager", "k", "warn", "Needs a fix", action={"kind": "x", "label": "Fix it"})
    assert [a["id"] for a in store.approvals(conn)] == [r["id"]]
    store.set_action(conn, r["id"], "approved")
    assert [a["id"] for a in store.approved_for(conn, "manager")] == [r["id"]]


def test_tickets_dedupe_and_finish(conn):
    t, new = tickets.file(conn, "manager", "researcher", "Find X")
    t2, new2 = tickets.file(conn, "manager", "researcher", "find x!")
    assert new and not new2 and t2["id"] == t["id"]
    assert not tickets.update(conn, t["id"], "accepted", by="manager")      # only the worker may accept
    assert tickets.update(conn, t["id"], "accepted", by="researcher")
    assert tickets.finish(conn, t["id"], "X", "the answer", by="researcher")
    inbox = signals.inbox(conn, "manager")
    assert inbox[-1]["kind"] == "deliverable" and inbox[-1]["src"] == "researcher"


def test_drops_pacing(conn):
    drops.add(conn, "first")
    drops.add(conn, "@researcher second")
    d1 = drops.release(conn, "manager", {"manager", "researcher"})
    assert d1["dst"] == "manager"
    assert drops.release(conn, "manager", {"manager", "researcher"}) is None   # spacing
    d2 = drops.release(conn, "manager", {"manager", "researcher"}, did=drops.queue(conn)[0]["id"])
    assert d2["dst"] == "researcher"


def test_password_hash():
    h = auth.hash_password("a long password here", n=2 ** 10)
    assert auth.check_password("a long password here", h) and not auth.check_password("nope", h)


def test_number_check():
    assert brain.unverified("up 12.5% to 1,200", "it went from 1,000 to 1,200, up 12.5%") == []
    assert brain.unverified("about 37 users", "we have 40 users") == [37.0]


def test_brain_budget_and_retry(tmp_path, monkeypatch):
    calls = []

    def fake(system, user, schema, model):
        calls.append(model)
        return ('{"text": "we have 99"}' if len(calls) == 1 else '{"text": "we have 40"}'), (10, 5), model
    monkeypatch.setattr("crew.config.DATA_DIR", tmp_path)
    b = brain.Brain("t", "sys", daily_calls=3, call=fake)
    b.path = tmp_path / "budget.json"
    assert b.ask("we have 40", {}, check_numbers=lambda r: [r["text"]]) == {"text": "we have 40"}
    assert b.usage()["calls"] == 2 and b.usage()["rejected"] == 1
    b.ask("x", {}); 
    with pytest.raises(brain.BudgetSpent):
        b.ask("x", {})


def test_roteo_provider(monkeypatch):
    """ROTEO_API_KEY wins; the schema goes into the instructions; fenced JSON still parses."""
    from crew.agents import brain
    assert brain.provider() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert brain.provider() == "anthropic"
    monkeypatch.setenv("ROTEO_API_KEY", "r")
    assert brain.provider() == "roteo"
    sent = {}

    class R:
        status_code = 200
        def json(self):
            return {"model": "claude-sonnet-5-5", "usage": {"prompt_tokens": 7, "completion_tokens": 3},
                    "choices": [{"finish_reason": "stop", "message": {"content": '```json\n{"hi": "there"}\n```'}}]}

    def post(url, json, headers, timeout):
        sent.update(url=url, body=json, auth=headers["Authorization"])
        return R()
    import httpx
    monkeypatch.setattr(httpx, "post", post)
    b = brain.Brain("t-roteo", "Be brief.", daily_calls=5)
    assert b.ask("say hi", {"type": "object", "properties": {"hi": {"type": "string"}}}) == {"hi": "there"}
    assert sent["url"].endswith("/chat/completions") and sent["auth"] == "Bearer r"
    assert '"hi"' in sent["body"]["messages"][0]["content"]
    assert b.usage()["in"] == 7
