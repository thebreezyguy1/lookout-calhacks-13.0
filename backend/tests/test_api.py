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
