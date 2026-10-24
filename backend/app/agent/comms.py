"""Read a text message or call caption for scam tactics with Claude.

Optional: the keyword tactics in rules.py work without it. When Claude is available its
reading is stored on the event (raw["classifier"]) and the rules use it, which catches
messages with no tell-tale keywords ("Hi Grandma, it's me, I'm in trouble").

Structured output validated with Pydantic; any failure returns None (rules only).
"""

import logging
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.agent.rules import TACTICS
from app.config import settings

log = logging.getLogger(__name__)

TACTIC_NAMES = tuple(name for name, _, _ in TACTICS)

SYSTEM_PROMPT = """You screen messages and phone-call captions received by an older adult, to spot scams.
Given the latest message and the earlier ones from the same sender, estimate how likely this
conversation is a scam in progress, and name the tactics present.
- Ordinary messages from family, doctors, pharmacies, friends and stores are not scams, even when they
  mention money or gift cards in a normal way.
- Grandparent, bank-fraud-department, IRS or police, tech-support, romance, prize and delivery scams
  rely on urgency, secrecy, impersonation and unusual payment methods.
- tactics: only those actually present in the messages. "authority" means the sender claims to BE a bank,
  government agency or police; a story that merely mentions the police is "family_emergency".
- reason: one short sentence a family member can understand."""


class MessageRead(BaseModel):
    likelihood: float = Field(ge=0.0, le=1.0)
    tactics: list[Literal[TACTIC_NAMES]]  # type: ignore[valid-type]
    reason: str


def classify_message(text: str, channel: str, sender: str, earlier: list[str],
                     client=None) -> MessageRead | None:
    if client is None:
        if not settings.anthropic_api_key:
            return None
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    kind = "phone call (live captions)" if channel == "call" else "text message"
    history = "\n".join(f"- {t}" for t in earlier[-8:]) or "(none)"
    try:
        response = client.messages.parse(
            model=settings.llm_model, max_tokens=4000, system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Channel: {kind}\nSender: {sender}\n"
                                                  f"Earlier from this sender:\n{history}\n\nLatest: {text}"}],
            output_format=MessageRead, output_config={"effort": "low"},
        )
        if response.parsed_output is None:
            raise ValueError(f"no structured output (stop_reason={getattr(response, 'stop_reason', None)})")
        return response.parsed_output
    except (ValidationError, ValueError) as exc:
        log.warning("Message classifier output invalid, using keyword rules only: %s", exc)
    except Exception as exc:  # noqa: BLE001 - any API/network failure falls back
        log.warning("Message classifier failed, using keyword rules only: %s", exc)
    return None
