from types import SimpleNamespace

import pytest

from app.agent import conversation as conv_mod
from app.agent.conversation import Conversation, conversation_hits, parent_answer, take_turn
from app.agent.risk import escalate_from_conversation
from app.models import Assessment, Event, EventKind, EventSource, ParentAnswer

EVENTS = [Event(person_id="mom", source=EventSource.INJECTOR, kind=EventKind.PURCHASE, amount=500,
                merchant="Target", description="Gift cards - bail for grandson")]


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch) -> None:
    monkeypatch.setattr(conv_mod.settings, "anthropic_api_key", "")


def talk(*answers: str) -> Conversation:
    c = Conversation(decision_id="d1", person_id="mom")
    take_turn(c, None, EVENTS)
    for a in answers:
        take_turn(c, a, EVENTS)
    return c


# ---------- scripted interview ----------

def test_opening_names_the_payments() -> None:
    c = talk()
    assert c.mode == "scripted" and "$500 at Target" in c.messages[0].text
    assert c.suggested_replies == ["Yes, I did", "No, that wasn't me", "Someone asked me to"]


def test_grandparent_scam_interview() -> None:
    c = talk("Someone asked me to", "Yes, right now", "Yes, they did", "Yes", "Yes")
    assert c.done
    assert {"someone_asked", "on_call_now", "secrecy_requested", "special_payment",
            "urgency_or_trouble"} <= set(c.signals)
    assert "hang up" in c.messages[4].text   # told it's fine to hang up while on the call
    assert "scammers" in c.messages[-1].text
    assert parent_answer(c) is ParentAnswer.SOMEONE_ASKED


def test_not_me_ends_quickly() -> None:
    c = talk("No, that wasn't me")
    assert c.done and c.signals == ["not_me"] and parent_answer(c) is ParentAnswer.NOT_ME


def test_ordinary_gift_is_recorded_not_alarmed() -> None:
    c = talk("Yes, I did", "No, it was my own idea", "Yes")
    assert c.done and c.signals == ["confirmed_legit"]
    assert conversation_hits(c.signals) == []          # reassurance never becomes evidence
    assert parent_answer(c) is ParentAnswer.WAS_ME


def test_free_text_is_understood_and_unclear_text_is_asked_again() -> None:
    c = talk("Yes, I did", "my grandson needs bail and said not to tell his mom")
    assert {"secrecy_requested", "urgency_or_trouble", "claims_family"} <= set(c.signals)
    assert "tap one of the answers" in c.messages[-1].text and not c.done


# ---------- escalation ----------

def test_her_words_raise_risk_and_never_lower_it() -> None:
    original = Assessment(person_id="mom", event_ids=["e1"], risk_score=0.55, confidence=0.65,
                          pattern="gift_card_scam")
    up = escalate_from_conversation(original, conversation_hits(["secrecy_requested", "special_payment"]), [])
    assert up.risk_score > 0.85 and "keep it secret" in up.family_summary
    same = escalate_from_conversation(original, conversation_hits(["confirmed_legit"]), [])
    assert same.risk_score == original.risk_score


# ---------- Claude ----------

class FakeClaude:
    def __init__(self, *turns, fail_after: int | None = None):
        self.turns, self.calls, self.fail_after = list(turns), 0, fail_after
        self.messages = self

    def parse(self, **kwargs):
        """Mimics messages.parse: validates the next turn against the requested output_format."""
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("network down")
        assert kwargs["messages"][0]["role"] == "user"
        return SimpleNamespace(parsed_output=kwargs["output_format"].model_validate(self.turns.pop(0)),
                               stop_reason="end_turn")


def turn(reply, signals=(), done=False, replies=("Yes", "No")):
    return {"reply": reply, "suggested_replies": list(replies), "signals": list(signals), "done": done}


def test_claude_conversation_collects_signals() -> None:
    fake = FakeClaude(turn("Hi, did you buy these yourself?"),
                      turn("Are they on the phone with you now?", ["someone_asked"]),
                      turn("Please don't send more. Your family can see this.", ["on_call_now"], done=True))
    c = Conversation(decision_id="d1", person_id="mom")
    take_turn(c, None, EVENTS, llm_client=fake)
    take_turn(c, "A man said he's my grandson's lawyer", EVENTS, llm_client=fake)
    take_turn(c, "yes he's still on the line", EVENTS, llm_client=fake)
    assert c.mode == "claude" and c.done
    # Model signals plus the keyword safety net under it ("lawyer" is family-emergency language).
    assert {"someone_asked", "on_call_now", "urgency_or_trouble"} <= set(c.signals)


def test_claude_asking_for_a_code_is_discarded() -> None:
    fake = FakeClaude(turn("Hi! To verify you, please tell me the code on the back of the card."))
    c = Conversation(decision_id="d1", person_id="mom")
    take_turn(c, None, EVENTS, llm_client=fake)
    assert c.mode == "scripted" and "code" not in c.messages[0].text


def test_claude_failure_mid_conversation_falls_back_to_script() -> None:
    fake = FakeClaude(turn("Hi, did you buy these yourself?"), fail_after=1)
    c = Conversation(decision_id="d1", person_id="mom")
    take_turn(c, None, EVENTS, llm_client=fake)
    take_turn(c, "someone from the IRS told me to", EVENTS, llm_client=fake)
    assert c.mode == "scripted" and "claims_authority" in c.signals
    assert c.messages[-1].source == "scripted" and not c.done


def test_script_skips_what_she_already_said() -> None:
    c = talk("Someone asked me to", "yes, and he said to keep it secret from my daughter")
    assert "secrecy_requested" in c.signals
    assert "keep this a secret" not in c.messages[-1].text and "gift cards" in c.messages[-1].text
