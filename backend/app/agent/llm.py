"""LLM pattern reasoning with Claude.

The model sees the person's baseline, the recent events and the rule hits, and
returns a structured assessment through a forced tool call. Output is validated
with Pydantic; anything invalid or any API failure returns None, and the engine
falls back to the rules-only result.

What the model may and may not do:
- It can raise or lower the risk with reasons, name the scam pattern, and write
  the plain-language explanation and the gentle check-in for the parent.
- It cannot take actions. Tiers are decided by the policy, not by the model.
"""

import json
import logging

from pydantic import BaseModel, Field, ValidationError

from app.config import settings
from app.models import Baseline, Event, RuleHit

log = logging.getLogger(__name__)

PATTERNS = [
    "grandparent_emergency", "tech_support_scam", "government_impersonation", "romance_scam",
    "prize_scam", "gift_card_scam", "crypto_or_wire_scam", "identity_theft", "payee_scam",
    "unusual_activity", "none",
]

SYSTEM_PROMPT = """You are Lookout, a fraud analyst protecting an older adult from financial scams.
You review a window of their recent financial and credit events, the deterministic rule signals,
and a baseline built from their real credit file. Decide whether these events look like a scam
pattern, how risky they are, and how sure you are.

Guidelines:
- Judge the pattern across events, not each event alone. Scams unfold as sequences: urgency,
  then gift cards or crypto or wires, often at several stores, often to a new payee.
- Ordinary spending is not suspicious just because it is large or new. Older adults buy
  gifts, pay contractors and travel. Avoid false alarms: they erode trust and dignity.
- A new credit inquiry or account on a dormant credit file (no new accounts in years,
  nothing open) is a strong identity-theft signal.
- Never invent facts not present in the input.
- family_summary: 2-3 plain sentences for the adult child, citing the specific events.
- parent_message: 1-3 short, warm, non-accusatory sentences addressed to the parent, asking
  them to confirm. Never shame. Never ask for passwords, codes or account numbers.
Always respond by calling the report_assessment tool."""

TOOL = {
    "name": "report_assessment",
    "description": "Report the scam-risk assessment for this window of events.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "enum": PATTERNS},
            "risk_score": {"type": "number", "minimum": 0, "maximum": 1,
                           "description": "Probability this window is a scam in progress."},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reasoning": {"type": "string", "description": "Analyst reasoning, 2-4 sentences."},
            "family_summary": {"type": "string"},
            "parent_message": {"type": "string"},
        },
        "required": ["pattern", "risk_score", "confidence", "reasoning", "family_summary",
                     "parent_message"],
    },
}


class LLMAssessment(BaseModel):
    pattern: str
    risk_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    family_summary: str
    parent_message: str


def build_prompt(baseline: Baseline | None, window: list[Event], hits: list[RuleHit]) -> str:
    events = [{
        "id": e.id, "when": e.occurred_at.isoformat(), "kind": e.kind.value, "amount": e.amount,
        "merchant": e.merchant, "counterparty": e.counterparty, "category": e.category,
        "description": e.description,
    } for e in sorted(window, key=lambda e: e.occurred_at)]
    payload = {
        "baseline": baseline.model_dump(mode="json", exclude={"request_ids", "built_at"}) if baseline else None,
        "events": events,
        "rule_signals": [h.model_dump() for h in hits],
    }
    return "Assess this window:\n" + json.dumps(payload, indent=2)


def llm_assess(baseline: Baseline | None, window: list[Event], hits: list[RuleHit],
               client=None) -> LLMAssessment | None:
    """Returns None (fall back to rules) if no API key, API error, or invalid output."""
    if client is None:
        if not settings.anthropic_api_key:
            return None
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    try:
        response = client.messages.create(
            model=settings.llm_model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=[TOOL],
            tool_choice={"type": "tool", "name": "report_assessment"},
            messages=[{"role": "user", "content": build_prompt(baseline, window, hits)}],
        )
        block = next(b for b in response.content if getattr(b, "type", None) == "tool_use")
        result = LLMAssessment.model_validate(block.input)
        if result.pattern not in PATTERNS:
            raise ValueError(f"unknown pattern {result.pattern!r}")
        return result
    except (StopIteration, ValidationError, ValueError) as exc:
        log.warning("LLM output invalid, falling back to rules: %s", exc)
    except Exception as exc:  # noqa: BLE001 - any API/network failure falls back
        log.warning("LLM call failed, falling back to rules: %s", exc)
    return None
