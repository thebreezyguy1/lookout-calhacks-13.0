"""Risk engine: deterministic rules first, then Claude's judgment on top.

How the two combine (kept simple so it can be explained to a judge):
- The rules produce a score from concrete, auditable signals.
- If Claude responds, the final score blends both: 60% Claude, 40% rules.
- Claude can lower the rules' score by at most 0.15. A strong rule signal can't
  be argued away entirely, which bounds the damage of a model mistake.
- If Claude is unavailable or returns invalid output, the rules result stands.
"""

from app.agent.llm import llm_assess
from app.agent.rules import combine, guess_pattern, rule_signals, rules_confidence
from app.models import Assessment, Baseline, Event, RuleHit

LLM_WEIGHT = 0.6
MAX_LLM_DISCOUNT = 0.15


def _rules_summary(hits) -> str:
    if not hits:
        return "No scam signals in this window."
    return "Signals: " + "; ".join(h.detail for h in hits) + "."


def assess_identity(person_id: str, hits: list[RuleHit]) -> Assessment:
    """Turn identity signals from the credit file into an assessment for the action policy.

    Kept separate from `assess` so a risky file isn't counted again on every window.
    """
    score = combine(hits)
    summary = ("Identity check on her credit file. " + _rules_summary(hits)) if hits else ""
    return Assessment(
        person_id=person_id,
        event_ids=[],
        risk_score=score,
        confidence=rules_confidence(hits),
        pattern="identity_theft" if hits else None,
        rule_hits=[h.rule for h in hits],
        signals=hits,
        reasoning=summary,
        family_summary=summary,
        parent_message=(
            "Hi, it's Lookout. Your credit file shows signs that someone else may be using your name "
            "or details. You haven't done anything wrong. Have you applied for any new credit recently?"
            if hits else ""
        ),
        engine="rules",
    )


def escalate_from_conversation(original: Assessment, hits: list[RuleHit], quotes: list[str]) -> Assessment:
    """Add what the parent said to an assessment. It can only raise the risk, never lower it."""
    signals = [*original.signals, *hits]
    # Stack her evidence on the original score (which may include Claude's judgment) with the same noisy-OR.
    score = 1 - (1 - original.risk_score) * (1 - combine(hits))
    said = "; ".join(h.detail for h in hits)
    summary = f"In the check-in conversation: {said}." + (f" {original.family_summary}" if original.family_summary else "")
    return original.model_copy(update={
        "risk_score": round(score, 4),
        "confidence": max(original.confidence, rules_confidence(signals)),
        "pattern": "identity_theft" if any(h.rule == "parent_not_me" for h in hits) else original.pattern,
        "rule_hits": [h.rule for h in signals],
        "signals": signals,
        "reasoning": summary + (f" Her words: \"{' / '.join(quotes)}\"" if quotes else ""),
        "family_summary": summary,
    })


def assess(person_id: str, window: list[Event], baseline: Baseline | None = None,
           history: list[Event] | None = None, *, use_llm: bool = True,
           llm_client=None) -> Assessment:
    hits = rule_signals(window, baseline, history)
    rules_score = combine(hits)
    base = Assessment(
        person_id=person_id,
        event_ids=[e.id for e in window],
        risk_score=rules_score,
        confidence=rules_confidence(hits),
        pattern=guess_pattern(hits),
        rule_hits=[h.rule for h in hits],
        signals=hits,
        reasoning=_rules_summary(hits),
        family_summary=_rules_summary(hits),
        parent_message=(
            "Hi, it's Lookout. We noticed some unusual activity on your account. "
            "Was this you? If someone asked you to make these payments, please call your family first."
            if hits else ""
        ),
        engine="rules",
    )
    if not use_llm or not window:
        return base

    llm = llm_assess(baseline, window, hits, client=llm_client)
    if llm is None:
        return base

    blended = LLM_WEIGHT * llm.risk_score + (1 - LLM_WEIGHT) * rules_score
    final = max(blended, rules_score - MAX_LLM_DISCOUNT)
    return base.model_copy(update={
        "risk_score": round(min(1.0, max(0.0, final)), 4),
        "confidence": round((llm.confidence + base.confidence) / 2, 2),
        "pattern": llm.pattern if llm.pattern != "none" else base.pattern,
        "reasoning": llm.reasoning,
        "family_summary": llm.family_summary,
        "parent_message": llm.parent_message or base.parent_message,
        "engine": "rules+llm",
    })
