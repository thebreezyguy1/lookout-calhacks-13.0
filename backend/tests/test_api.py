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
    assert "Read this to me" in client.get("/app/mom.html").text


class FakeCRS:
    """Stands in for the CRS sandbox: a quiet file everywhere, deceased at one bureau, risky contact details."""

    def credit_report(self, body, bureau):
        from app.integrations.crs import CRSError, CRSResponse
        if bureau == "equifax":
            raise CRSError("E1200: deceased file", ["E1200"])
        return CRSResponse(body={"tradelines": [{"accountOpenedDate": "1998-05-01",
                                                 "accountStatusType": "Closed"}]}, request_id="r1")

    def fraud_finder(self, body):
        from app.integrations.crs import CRSResponse
        return CRSResponse(body={"risk": {"score": 88}}, request_id="r2")


def test_baseline_runs_identity_check_through_policy(monkeypatch) -> None:
    import app.main as main
    monkeypatch.setattr(main, "CRSClient", FakeCRS)
    b = client.post("/people/mom/baseline", params={"persona": "P14"}).json()
    assert b["identity_level"] == "flag" and b["dormant_file"] is True
    assert {"deceased_identity", "fraud_finder_high"} <= {h["rule"] for h in b["identity_signals"]}

    decision = client.get("/people/mom/decisions").json()[0]
    assert decision["assessment"]["pattern"] == "identity_theft"
    assert decision["tier"] == "recommend_hold" and decision["requires_approval"] is True
    actions = [r["action"] for r in client.get("/people/mom/audit").json()]
    assert actions == ["baseline_built", "assessed"]


def test_personas_are_listed() -> None:
    ids = [p["id"] for p in client.get("/personas").json()]
    assert len(ids) == 20 and "P20" in ids


def test_conversation_escalates_through_policy(monkeypatch) -> None:
    import app.agent.conversation as conv_mod
    monkeypatch.setattr(conv_mod.settings, "anthropic_api_key", "")   # scripted, never the real API
    client.post("/people/mom/inject/scam-quiet-gift-cards")
    first = client.post("/people/mom/assess", params={"use_llm": False}).json()
    check_in = client.get("/people/mom/check-in").json()
    assert check_in["decision_id"] == first["id"]

    url = f"/decisions/{first['id']}/conversation"
    opened = client.post(url, json={}).json()
    assert client.post(url, json={}).json() == opened          # opening is idempotent
    for answer in ["Someone asked me to", "No", "Yes, they did"]:
        view = client.post(url, json={"message": answer}).json()
    assert {"someone_asked", "secrecy_requested"} <= set(view["signals"]) and not view["done"]

    latest = client.get("/people/mom/decisions").json()[0]
    assert latest["conversation_of"] == first["id"]
    assert latest["assessment"]["risk_score"] > first["assessment"]["risk_score"]
    assert latest["tier"] == "recommend_hold" and latest["requires_approval"] is True
    assert client.get("/people/mom/check-in").json()["decision_id"] == first["id"]   # still the same chat

    for answer in ["Yes", "Yes"]:
        view = client.post(url, json={"message": answer}).json()
    assert view["done"]
    assert client.get("/people/mom/check-in").json() is None
    assert client.get(f"/decisions/{latest['id']}/conversation").json()["done"] is True
    actions = [r["action"] for r in client.get("/people/mom/audit").json()]
    assert "conversation_turn" in actions and actions[-1] == "parent_responded"
