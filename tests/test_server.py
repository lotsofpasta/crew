import pytest
from fastapi.testclient import TestClient

from crew import auth, server

M = {"Authorization": "Bearer " + "m" * 32}
R = {"Authorization": "Bearer " + "r" * 32}


@pytest.fixture
def client():
    auth.reset_limits()
    return TestClient(server.app)


def owner(client):
    tok = client.post("/api/login", json={"password": "correct horse battery"}).json()["token"]
    return {"Authorization": "Bearer " + tok}


def test_login_and_roles(client):
    assert client.get("/api/whoami").status_code == 401                   # not from localhost, no token
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    h = owner(client)
    assert client.get("/api/whoami", headers=h).json()["role"] == "owner"
    assert client.post("/api/exchange/say", json={"to": "manager", "text": "hi"},
                       headers={"Authorization": "Bearer " + "v" * 30}).status_code == 403   # viewer cannot write
    assert client.get("/api/whoami", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_unknown_host_rejected(client):
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 421


def test_agent_token_scoped(client):
    assert client.post("/api/agents/manager/sync", json={}).status_code == 401
    assert client.post("/api/agents/manager/sync", json={}, headers=R).status_code == 401     # another agent's token
    assert client.post("/api/agents/nobody/sync", json={}, headers=M).status_code == 404
    assert client.get("/api/agents", headers=M).status_code == 401                            # agent token != dashboard


def test_full_round_trip(client):
    h = owner(client)
    # owner -> manager message
    client.post("/api/exchange/say", json={"to": "manager", "text": "Please look into X"}, headers=h)
    r = client.post("/api/agents/manager/sync", json={"status": {"doing": "reading"}, "cursor": 0}, headers=M).json()
    msg = [m for m in r["messages"] if m["src"] == "you"][-1]
    assert msg["label"] == "Please look into X"
    cur = r["cursor"]
    # manager files a ticket + opens an issue with an action; a resend of the same ops is ignored
    ops = [{"n": 1, "op": "ticket", "to": "researcher", "title": "Look into X"},
           {"n": 2, "op": "issue", "key": "k", "level": "warn", "title": "Need approval",
            "action": {"kind": "nudge", "label": "Do the thing"}}]
    r = client.post("/api/agents/manager/sync", json={"cursor": cur, "ops": ops}, headers=M).json()
    assert r["applied"] == 2 and not r["errors"]
    assert client.post("/api/agents/manager/sync", json={"cursor": cur, "ops": ops}, headers=M).json()["applied"] == 0
    # researcher works the ticket
    rr = client.post("/api/agents/researcher/sync", json={}, headers=R).json()
    tid = rr["tickets"][0]["id"]
    rr = client.post("/api/agents/researcher/sync", json={"ops": [
        {"n": 1, "op": "accept", "id": tid}, {"n": 2, "op": "finish", "id": tid, "title": "X", "body": "answer"}]}, headers=R).json()
    assert rr["applied"] == 2
    # owner approves; manager sees it, reports done
    ag = client.get("/api/agents", headers=h).json()
    rid = ag["approvals"][0]["id"]
    assert client.post(f"/api/agents/reports/{rid}/approve", headers=h).status_code == 200
    r = client.post("/api/agents/manager/sync", json={"cursor": cur}, headers=M).json()
    assert [a["id"] for a in r["approved"]] == [rid]
    assert any(m["kind"] == "deliverable" and m["src"] == "researcher" for m in r["messages"])
    r = client.post("/api/agents/manager/sync", json={"cursor": r["cursor"], "ops": [
        {"n": 3, "op": "action_done", "id": rid, "ok": True, "note": "done"}]}, headers=M).json()
    assert r["approved"] == []
    # the visualizer contract
    v = client.get("/api/viz", headers=h).json()
    ids = {n["id"] for n in v["nodes"]}
    assert {"manager", "researcher", "you", "llm"} <= ids
    assert {"from": "researcher", "to": "manager", "type": "reports_to"} in v["edges"]
    assert v["pulses"] and v["cursor"] == v["pulses"][-1]["id"]
    assert client.get(f"/api/viz?after={v['cursor']}", headers=h).json()["pulses"] == []


def test_drop_goes_to_manager(client):
    h = owner(client)
    r = client.post("/api/exchange/drop", json={"text": "https://example.com worth a read"}, headers=h)
    assert r.status_code == 200
    got = client.post("/api/agents/manager/sync", json={}, headers=M).json()
    assert any(m["kind"] == "drop" for m in got["messages"])


def test_pages_and_static(client):
    for p in ("/", "/exchange", "/visualizer", "/static/app.js", "/static/viz-data.js"):
        assert client.get(p).status_code == 200, p
    assert client.get("/static/../crew/server.py").status_code == 404
