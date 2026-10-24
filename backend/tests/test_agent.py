from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from app.agent.baseline import build_baseline, diff_reports
from app.agent.policy import decide
from app.agent.risk import assess
from app.agent.rules import combine, rule_signals
from app.models import Baseline, Event, EventKind, EventSource, Tier

NOW = datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)


def ev(kind: EventKind, minutes: int = 0, **kw) -> Event:
    return Event(person_id="mom", source=EventSource.INJECTOR, kind=kind,
                 occurred_at=NOW + timedelta(minutes=minutes), **kw)


DORMANT = Baseline(person_id="mom", total_accounts=6, open_accounts=0,
                   months_since_last_new_account=60, dormant_file=True)

# Shaped like P20's real Experian report: six closed tradelines, no inquiries.
STALE_REPORT = {
    "creditFiles": [{"creditFileDetail": {"creditFileResultStatusType": "FileReturned"}}],
    "tradelines": [{"creditorName": f"BANK {i}", "accountIdentifier": str(i),
                    "accountOpenedDate": "1998-05-01", "lastActivityDate": "2019-02-01",
                    "accountStatusType": "Closed"} for i in range(6)],
    "inquiries": [],
    "scores": [{"modelName": "VantageScore 4.0", "scoreValue": None}],
}


# ---------- baseline ----------

def test_stale_file_is_dormant() -> None:
    b = build_baseline("mom", STALE_REPORT, {"risk": {"score": 8}}, today=date(2026, 10, 24))
    assert b.dormant_file is True
    assert b.total_accounts == 6 and b.open_accounts == 0
    assert b.fraud_score == 8 and b.score is None
    assert b.file_status == "FileReturned"


def test_active_file_is_not_dormant() -> None:
    report = dict(STALE_REPORT, tradelines=[{"accountOpenedDate": "2026-03-01",
                                             "accountStatusType": "Open"}])
    assert build_baseline("mom", report, today=date(2026, 10, 24)).dormant_file is False


def test_diff_reports_finds_new_inquiry_and_account() -> None:
    current = dict(STALE_REPORT,
                   inquiries=[{"creditorName": "CAPITAL BANK", "inquiryDate": "2026-10-23",
                               "businessType": "Banking"}],
                   tradelines=STALE_REPORT["tradelines"] + [{
                       "creditorName": "CAPITAL BANK", "accountIdentifier": "99",
                       "accountOpenedDate": "2026-10-24", "accountType": "Revolving",
                       "creditLimitAmount": "5000"}])
    kinds = sorted(e.kind.value for e in diff_reports("mom", STALE_REPORT, current))
    assert kinds == ["credit_inquiry", "new_credit_account"]


# ---------- rules ----------

def test_gift_cards_at_several_stores() -> None:
    window = [ev(EventKind.PURCHASE, 0, amount=500, merchant="Target", description="Gift cards"),
              ev(EventKind.PURCHASE, 60, amount=500, merchant="Walgreens", description="Google Play card")]
    names = {h.rule for h in rule_signals(window)}
    assert {"gift_card_purchases", "gift_cards_multiple_stores_24h"} <= names


def test_small_birthday_gift_card_stays_low() -> None:
    window = [ev(EventKind.PURCHASE, amount=50, merchant="Target", description="Gift card - birthday")]
    assert decide(assess("mom", window, DORMANT, use_llm=False)).tier is Tier.LOG


def test_new_credit_weighs_more_on_dormant_file() -> None:
    window = [ev(EventKind.CREDIT_INQUIRY, counterparty="CAPITAL BANK")]
    active = Baseline(person_id="mom", dormant_file=False)
    assert combine(rule_signals(window, DORMANT)) > combine(rule_signals(window, active))
    assert decide(assess("mom", window, DORMANT, use_llm=False)).tier is Tier.ALERT


def test_deceased_identity_flag() -> None:
    dead = Baseline(person_id="x", bureau_errors=["E1200: This is a deceased file"])
    hits = rule_signals([ev(EventKind.CREDIT_INQUIRY)], dead)
    assert "deceased_identity_in_use" in {h.rule for h in hits}


def test_no_events_no_risk() -> None:
    a = assess("mom", [], DORMANT, use_llm=False)
    assert a.risk_score == 0 and decide(a).tier is Tier.LOG


# ---------- LLM layer ----------

class FakeClaude:
    def __init__(self, payload=None, fail=False):
        self.payload, self.fail = payload, fail
        self.messages = self

    def create(self, **kwargs):
        if self.fail:
            raise RuntimeError("network down")
        assert kwargs["tool_choice"] == {"type": "tool", "name": "report_assessment"}
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=self.payload)])


SCAM_WINDOW = [ev(EventKind.PURCHASE, 0, amount=500, merchant="Target", description="Gift cards - bail"),
               ev(EventKind.PURCHASE, 50, amount=500, merchant="CVS", description="Vanilla prepaid card")]


def test_llm_failure_falls_back_to_rules() -> None:
    a = assess("mom", SCAM_WINDOW, DORMANT, llm_client=FakeClaude(fail=True))
    assert a.engine == "rules" and a.risk_score > 0.65


def test_llm_invalid_output_falls_back_to_rules() -> None:
    a = assess("mom", SCAM_WINDOW, DORMANT, llm_client=FakeClaude({"pattern": "grandparent_emergency"}))
    assert a.engine == "rules"


def test_llm_result_is_blended_and_bounded() -> None:
    rules_only = assess("mom", SCAM_WINDOW, DORMANT, use_llm=False)
    calm = {"pattern": "none", "risk_score": 0.0, "confidence": 0.9, "reasoning": "r",
            "family_summary": "f", "parent_message": "p"}
    a = assess("mom", SCAM_WINDOW, DORMANT, llm_client=FakeClaude(calm))
    assert a.engine == "rules+llm"
    # Claude can't argue a strong rule signal away by more than 0.15.
    assert a.risk_score >= rules_only.risk_score - 0.15 - 1e-9
    assert a.pattern == rules_only.pattern  # "none" keeps the rules' pattern
