import pytest

from app.agent.policy import decide
from app.models import Assessment, Tier


def _assessment(risk: float, confidence: float = 0.9) -> Assessment:
    return Assessment(person_id="p1", event_ids=["e1"], risk_score=risk, confidence=confidence)


@pytest.mark.parametrize(("risk", "tier"), [
    (0.10, Tier.LOG),
    (0.50, Tier.CHECK_IN),
    (0.70, Tier.ALERT),
    (0.95, Tier.RECOMMEND_HOLD),
])
def test_tiers(risk: float, tier: Tier) -> None:
    assert decide(_assessment(risk)).tier is tier


def test_hold_always_requires_approval() -> None:
    decision = decide(_assessment(0.99))
    assert decision.tier is Tier.RECOMMEND_HOLD
    assert decision.requires_approval is True


def test_low_confidence_never_escalates_past_check_in() -> None:
    assert decide(_assessment(0.99, confidence=0.3)).tier is Tier.CHECK_IN
