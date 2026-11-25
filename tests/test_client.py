from fastapi.testclient import TestClient

from crew import auth, server
from crew.agents.client import Agent


def agent(key, token):
    a = Agent(key, url="http://testserver", token=token)
    a.http = TestClient(server.app, headers={"Authorization": f"Bearer {token}"})
    return a


def test_client_round_trip_and_flush():
    auth.reset_limits()
    m, r = agent("manager", "m" * 32), agent("researcher", "r" * 32)
    got = []
    r.on_ticket(lambda t: (r.accept(t["id"]), r.finish(t["id"], "Answer", "42")))
    m.on_message(lambda msg: got.append(msg))

    m.step()                                     # learn the cursor
    m.ticket("researcher", "Client test: what is the answer?")
    m.say("researcher", "hello from the client test")
    assert m.flush() and not m.outbox            # sent without reading the inbox
    r.step()                                     # researcher accepts + finishes
    m.step()
    kinds = [(g["src"], g["kind"]) for g in got]
    assert ("researcher", "accept") in kinds and ("researcher", "deliverable") in kinds


def test_issue_and_approved_action():
    auth.reset_limits()
    m = agent("manager", "m" * 32)
    m.step()
    done = []
    m.on_approved(lambda rep: (done.append(rep["id"]), m.action_done(rep["id"], True, "ok")))
    m.issue("client-test", "Needs approval", action={"kind": "x", "label": "Do X"})
    m.step()
    c = TestClient(server.app)
    tok = c.post("/api/login", json={"password": "correct horse battery"}).json()["token"]
    h = {"Authorization": "Bearer " + tok}
    rid = next(a["id"] for a in c.get("/api/agents", headers=h).json()["approvals"] if a["key"] == "client-test")
    c.post(f"/api/agents/reports/{rid}/approve", headers=h)
    m.step()
    assert done == [rid]
    assert c.get("/api/agents", headers=h).json()["approvals"] == [] or all(a["id"] != rid for a in c.get("/api/agents", headers=h).json()["approvals"])
