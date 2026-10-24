"""Action policy: maps a risk assessment to an escalation tier.

Every action Lookout takes goes through `decide`. Thresholds are placeholders
to be tuned against the eval set in `evals/`.
"""

from app.models import Assessment, Decision, Tier

# (minimum risk score, tier), checked from highest to lowest.
THRESHOLDS: list[tuple[float, Tier]] = [
    (0.85, Tier.RECOMMEND_HOLD),
    (0.65, Tier.ALERT),
    (0.40, Tier.CHECK_IN),
    (0.00, Tier.LOG),
]

# Below this confidence, never escalate past a check-in: ask, don't accuse.
MIN_CONFIDENCE_TO_ALERT = 0.6


def decide(assessment: Assessment) -> Decision:
    tier = next(t for threshold, t in THRESHOLDS if assessment.risk_score >= threshold)

    if assessment.confidence < MIN_CONFIDENCE_TO_ALERT and tier in (Tier.ALERT, Tier.RECOMMEND_HOLD):
        tier = Tier.CHECK_IN

    return Decision(
        assessment=assessment,
        tier=tier,
        # Hard rule: a recommended hold always needs a human.
        requires_approval=tier is Tier.RECOMMEND_HOLD,
    )
