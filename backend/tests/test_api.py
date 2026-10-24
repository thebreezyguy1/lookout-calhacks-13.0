from fastapi.testclient import TestClient

from app.main import app
from app.models import Baseline
from app.store import store

client = TestClient(app)


def setup_function() -> None:
    store.reset()
    store.baselines["mom"] = Baseline(person_id="mom", persona_id="P20", total_accounts=6,
                                      open_accounts=0, months_since_last_new_account=60,
                                      dormant_file=True)


def test_scenarios_are_listed() -> None:
    ids = {s["id"] for s in client.get("/scenarios").json()}
    assert "scam-grandparent-bail" in ids and "normal-weekly-errands" in ids


def test_inject_assess_and_approve_flow() -> None:
    injected = client.post("/people/mom/inject/scam-grandparent-bail").json()
    assert len(injected) == 3 and all(e["source"] == "injector" for e in injected)

    decision = client.post("/people/mom/assess", params={"use_llm": False}).json()
    assert decision["tier"] == "recommend_hold"
    assert decision["requires_approval"] is True
    assert decision["approved"] is None

    reviewed = client.post(f"/decisions/{decision['id']}/review",
                           json={"approve": True, "reviewer": "daughter"}).json()
    assert reviewed["approved"] is True

    actions = [r["action"] for r in client.get("/people/mom/audit").json()]
    assert actions == ["scenario_injected", "assessed", "approved"]


def test_normal_scenario_stays_quiet() -> None:
    client.post("/people/mom/inject/normal-weekly-errands")
    decision = client.post("/people/mom/assess", params={"use_llm": False}).json()
    assert decision["tier"] == "log"


def test_review_rejected_when_no_approval_needed() -> None:
    client.post("/people/mom/inject/normal-weekly-errands")
    decision = client.post("/people/mom/assess", params={"use_llm": False}).json()
    resp = client.post(f"/decisions/{decision['id']}/review", json={"approve": True, "reviewer": "x"})
    assert resp.status_code == 400


def test_reset_keeps_baseline() -> None:
    client.post("/people/mom/inject/scam-tech-support")
    client.post("/people/mom/reset")
    assert client.get("/people/mom/events").json() == []
    assert client.get("/people/mom/baseline").status_code == 200


def test_parent_check_in_flow() -> None:
    assert client.get("/people/mom/check-in").json() is None

    client.post("/people/mom/inject/scam-grandparent-bail")
    decision = client.post("/people/mom/assess", params={"use_llm": False}).json()
    assert decision["assessment"]["signals"]  # evidence for the dashboard

    check_in = client.get("/people/mom/check-in").json()
    assert check_in["decision_id"] == decision["id"]
    assert len(check_in["events"]) == 3 and check_in["message"]

    answered = client.post(f"/decisions/{decision['id']}/parent-response",
                           json={"answer": "someone_asked"}).json()
    assert answered["parent_response"] == "someone_asked"
    assert answered["tier"] == decision["tier"]  # an answer never changes the tier by itself
    assert client.get("/people/mom/check-in").json() is None
    assert client.get("/people/mom/audit").json()[-1]["action"] == "parent_responded"


def test_quiet_window_never_asks_the_parent() -> None:
    client.post("/people/mom/inject/normal-weekly-errands")
    client.post("/people/mom/assess", params={"use_llm": False})
    assert client.get("/people/mom/check-in").json() is None


def test_frontend_is_served() -> None:
    assert "Family dashboard" in client.get("/app/").text
    assert "Someone asked me to do this" in client.get("/app/mom.html").text
