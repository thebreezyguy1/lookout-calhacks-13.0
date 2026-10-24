"""The check-in conversation: Lookout talks with the parent while a scam may be in progress.

Scams work because the scammer keeps the victim isolated, rushed and secretive. A
notification doesn't break that; a calm conversation that asks the right questions can.
The parent's answers are also the strongest evidence there is ("they told me not to tell
my daughter"), so they become RuleHits and go back through the action policy.

Two engines, same contract:
- Claude (when ANTHROPIC_API_KEY is set): one question at a time, grounded in the evidence,
  returning a structured turn through a forced tool call, validated with Pydantic.
- A scripted interview (no key, or any Claude failure): a fixed tree of yes/no questions.

What the conversation may and may not do:
- It can only ADD evidence. Nothing the parent says lowers the risk on its own, because
  scammers coach victims to say "it's for me". Reassurance is recorded for the family.
- It never asks for passwords, PINs, codes, card or account numbers, and never tells the
  parent to move money. Claude replies that look like they ask for those are discarded.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.agent.rules import AUTHORITY, CRYPTO_OR_WIRE, FAMILY_EMERGENCY, GIFT_CARD, REMOTE_ACCESS
from app.config import settings
from app.models import Event, EventKind, ParentAnswer, RuleHit

log = logging.getLogger(__name__)

MAX_TURNS = 8

# What the parent can tell us, and how much it weighs. Weights combine with the same noisy-OR.
SIGNALS: dict[str, tuple[float, str]] = {
    "not_me": (0.75, "Mom says she didn't make these payments"),
    "someone_asked": (0.4, "Mom says someone asked her to make these payments"),
    "on_call_now": (0.3, "Mom was on the phone or computer with them during the check-in"),
    "secrecy_requested": (0.6, "Mom says they told her to keep it secret from family or the bank"),
    "special_payment": (0.6, "Mom says they asked for gift cards, card numbers, crypto, a wire or a courier"),
    "urgency_or_trouble": (0.4, "Mom says they said it was urgent or a loved one was in trouble"),
    "claims_authority": (0.4, "Mom says the caller claimed to be the bank, the government or the police"),
    "claims_family": (0.3, "Mom says the caller claimed to be a family member"),
    "remote_access": (0.5, "Mom says they had her install an app or took over her computer"),
}
REASSURANCE = "confirmed_legit"   # recorded, never lowers risk
ALL_SIGNALS = [*SIGNALS, REASSURANCE]
SCAM_SIGNALS = set(SIGNALS) - {"not_me"}


class Message(BaseModel):
    role: Literal["lookout", "parent"]
    text: str
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str | None = None        # "claude" or "scripted" for Lookout turns


class Conversation(BaseModel):
    decision_id: str                 # the check-in decision that started it
    person_id: str
    mode: Literal["claude", "scripted"] = "scripted"
    step: str | None = "start"       # scripted node; None when done
    messages: list[Message] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    suggested_replies: list[str] = Field(default_factory=list)
    done: bool = False
    escalations: list[str] = Field(default_factory=list)   # decision ids created from this conversation

    def add_signals(self, names: list[str]) -> list[str]:
        new = [n for n in names if n in ALL_SIGNALS and n not in self.signals]
        self.signals += new
        return new


def conversation_hits(signals: list[str]) -> list[RuleHit]:
    return [RuleHit(rule=f"parent_{s}", weight=SIGNALS[s][0], detail=SIGNALS[s][1])
            for s in signals if s in SIGNALS]


def parent_answer(conv: Conversation) -> ParentAnswer | None:
    """Summarize the conversation as the dashboard's one-line answer."""
    if "not_me" in conv.signals:
        return ParentAnswer.NOT_ME
    if set(conv.signals) & SCAM_SIGNALS:
        return ParentAnswer.SOMEONE_ASKED
    if REASSURANCE in conv.signals or conv.done:
        return ParentAnswer.WAS_ME
    return None


def describe_events(events: list[Event]) -> str:
    def one(e: Event) -> str:
        if e.kind is EventKind.TEXT_MESSAGE:
            return f"a text from {e.counterparty or 'an unknown number'}"
        if e.kind is EventKind.CALL_TRANSCRIPT:
            return f"a call from {e.counterparty or 'an unknown number'}"
        amount = f"${e.amount:,.0f}" if e.amount else ""
        where = e.merchant or e.counterparty or ""
        return " at ".join(p for p in (amount, where) if p) or (e.description or "a payment")
    parts = list(dict.fromkeys(one(e) for e in events))
    more = f" and {len(parts) - 3} more" if len(parts) > 3 else ""
    return (", ".join(parts[:3]) + more) if parts else "some unusual activity on your account"


def is_money(e: Event) -> bool:
    return e.kind not in (EventKind.TEXT_MESSAGE, EventKind.CALL_TRANSCRIPT)


# ---------- scripted interview ----------

YES = re.compile(r"^\s*(yes|yeah|yep|yup|i did|correct|right|sure|uh huh|they did|they are|i am)\b", re.I)
NO = re.compile(r"^\s*(no|nope|nah|not really|never|i didn't|they didn't|it was my own|i'?m not sure|not sure)\b", re.I)


class Node(BaseModel):
    text: str
    replies: list[str]
    on_yes: str
    on_no: str
    yes_signals: list[str] = []
    no_signals: list[str] = []
    yes_prefix: str = ""


SCRIPT: dict[str, Node] = {
    "start": Node(text="Hi, it's Lookout. I noticed {events}. Did you make {these} yourself?",
                  replies=["Yes, I did", "No, that wasn't me", "Someone asked me to"],
                  on_yes="who_asked", on_no="end_not_me", no_signals=["not_me"]),
    # Opening when what Lookout saw is a text or call, not a payment.
    "start_comms": Node(text="Hi, it's Lookout. I saw {events}, and it looks like it could be a scam. "
                             "Has someone asked you to send money, buy gift cards or share a code?",
                        replies=["Yes, they have", "No", "I'm not sure"],
                        on_yes="on_call", on_no="known_sender", yes_signals=["someone_asked"]),
    "known_sender": Node(text="Do you know this person, and have you met them in real life?",
                         replies=["Yes, I know them", "No"], on_yes="end_legit", on_no="secret",
                         yes_signals=[REASSURANCE]),
    "who_asked": Node(text="Thank you. Did someone call, text or email you and ask you to make them?",
                      replies=["Yes, someone asked me", "No, it was my own idea"],
                      on_yes="on_call", on_no="known", yes_signals=["someone_asked"]),
    "known": Node(text="Are they for someone you know in person, like a gift or a bill you were expecting?",
                  replies=["Yes", "No"], on_yes="end_legit", on_no="secret", yes_signals=[REASSURANCE]),
    "on_call": Node(text="Are you talking with them right now, on the phone or on the computer?",
                    replies=["Yes, right now", "No"], on_yes="secret", on_no="secret",
                    yes_signals=["on_call_now"],
                    yes_prefix="It's OK to hang up. You can say \"I'll call you back\" and put the phone down. "
                               "Nobody honest will mind. "),
    "secret": Node(text="Did they tell you to keep this a secret, or not to tell your family or the bank?",
                   replies=["Yes, they did", "No"], on_yes="payment", on_no="payment",
                   yes_signals=["secrecy_requested"]),
    "payment": Node(text="Did they ask you to buy gift cards, read them the numbers on the back, "
                         "or send money by crypto, wire or courier?",
                    replies=["Yes", "No"], on_yes="urgency", on_no="urgency",
                    yes_signals=["special_payment"]),
    "urgency": Node(text="Did they say it was urgent, or that someone you love is in trouble?",
                    replies=["Yes", "No"], on_yes="end", on_no="end", yes_signals=["urgency_or_trouble"]),
}

ENDINGS = {
    "end_not_me": "Thank you for telling me. Someone else may be using your account or your name. "
                  "You haven't done anything wrong. Your family can see this now and will reach out. "
                  "Please don't answer calls or messages about these payments.",
    "end_legit": "Thank you, that's all I needed. Have a lovely day.",
    "end_scam": "Thank you for telling me. What you're describing is how scammers work: they rush you, "
                "ask for gift cards or crypto, and ask you to keep it secret. Please don't send anything "
                "more, even if they call back. A real grandchild, bank or police officer will never be "
                "upset that you checked. Your family can see this now. Please call them.",
    "end_unsure": "Thank you. Nothing you said worries me, but your family can see these payments too, "
                  "just in case. If anyone asks you for more money, please call your family first.",
}

# Free text the scripted interview can still learn from.
KEYWORDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"secret|don'?t tell|not to tell|embarrass", re.I), "secrecy_requested"),
    (GIFT_CARD, "special_payment"), (CRYPTO_OR_WIRE, "special_payment"),
    (re.compile(r"numbers? on the back|courier|pick(ed)? up", re.I), "special_payment"),
    (FAMILY_EMERGENCY, "urgency_or_trouble"), (re.compile(r"urgent|right away|hurry", re.I), "urgency_or_trouble"),
    (AUTHORITY, "claims_authority"), (re.compile(r"\bbank\b.*(fraud|investigat|security)", re.I), "claims_authority"),
    (re.compile(r"grandson|granddaughter|nephew|niece", re.I), "claims_family"),
    (REMOTE_ACCESS, "remote_access"),
]


def keyword_signals(text: str) -> list[str]:
    return list(dict.fromkeys(s for pattern, s in KEYWORDS if pattern.search(text)))


def _ending(conv: Conversation) -> str:
    if "not_me" in conv.signals:
        return ENDINGS["end_not_me"]
    if set(conv.signals) & SCAM_SIGNALS:
        return ENDINGS["end_scam"]
    if REASSURANCE in conv.signals:
        return ENDINGS["end_legit"]
    return ENDINGS["end_unsure"]


def _say(conv: Conversation, text: str, replies: list[str], source: str) -> None:
    conv.messages.append(Message(role="lookout", text=text, source=source))
    conv.suggested_replies = replies


def _finish(conv: Conversation, source: str, prefix: str = "") -> None:
    _say(conv, prefix + _ending(conv), [], source)
    conv.done, conv.step = True, None


def scripted_turn(conv: Conversation, parent_text: str | None, events: list[Event]) -> None:
    if parent_text is None:   # opening line
        node = SCRIPT[conv.step or "start"]
        these = "this payment" if len(events) == 1 else "these payments"
        _say(conv, node.text.format(events=describe_events(events), these=these), node.replies, "scripted")
        return

    node = SCRIPT[conv.step]
    conv.add_signals(keyword_signals(parent_text))
    choice = parent_text.strip().lower()
    if conv.step == "start" and choice.startswith("someone asked"):
        answer = "yes_asked"
    elif YES.match(choice):
        answer = "yes"
    elif NO.match(choice):
        answer = "no"
    else:
        _say(conv, "Thank you. To be sure I understand, could you tap one of the answers below?",
             node.replies, "scripted")
        return

    if answer == "yes_asked":                     # skip "did someone ask you?"
        conv.add_signals(["someone_asked"])
        nxt, prefix = "on_call", ""
    elif answer == "yes":
        conv.add_signals(node.yes_signals)
        nxt, prefix = node.on_yes, node.yes_prefix
    else:
        conv.add_signals(node.no_signals)
        nxt, prefix = node.on_no, ""

    # Don't ask what she already told us in her own words.
    while not nxt.startswith("end") and SCRIPT[nxt].yes_signals \
            and all(s in conv.signals for s in SCRIPT[nxt].yes_signals):
        nxt = SCRIPT[nxt].on_yes

    if nxt.startswith("end"):
        _finish(conv, "scripted", prefix)
    else:
        conv.step = nxt
        nxt_node = SCRIPT[nxt]
        _say(conv, prefix + nxt_node.text, nxt_node.replies, "scripted")


# ---------- Claude ----------

SYSTEM_PROMPT = """You are Lookout, speaking with an older adult on a large-text screen during a possible scam.
Their family set Lookout up with their consent. You already know what Lookout noticed (payments, or
texts and calls they received), listed in the context. If it was a text or call, refer to it plainly
("the text from that number") without repeating anything like a code or link it contained.

Your job, in order:
1. Find out calmly whether they made these payments, and if someone asked them to.
2. If someone did, gently ask the questions that reveal a scam, one per turn: are they on the phone with
   that person right now (if so, tell them it's fine to hang up); were they told to keep it secret; were they
   asked for gift cards, the numbers on a card, crypto, a wire or a courier; were they told it was urgent or
   that a loved one is in trouble; did the person claim to be family, the bank, the government or police.
3. End with clear, kind advice: don't send anything more, scammers rush and isolate people, and their
   family can see this now.

Rules:
- One short question per turn, 1-3 sentences, plain words, warm, never shaming or accusing.
- Never ask for passwords, PINs, verification codes, card numbers, account numbers or SSNs.
- Never tell them to send, move or withdraw money.
- signals: only what the parent's latest message actually says. Use confirmed_legit only when they explain
  a plausible ordinary reason (a gift for a known person, an expected bill).
- suggested_replies: 2-3 short answers they could tap, under 40 characters each; empty when done.
- reply: your next message to them.
- done: true on your final, advice-giving message."""

FORBIDDEN = re.compile(
    r"\b(tell|give|send|share|read|type|enter|say)\b[^.?!]{0,40}\b(me|us|lookout)\b[^.?!]{0,40}"
    r"\b(password|pin|passcode|code|card number|account number|social security|ssn)\b"
    r"|\b(what|which) is your\b[^.?!]{0,30}\b(password|pin|code|account number|social security)\b",
    re.I,
)


class ClaudeTurn(BaseModel):
    reply: str = Field(min_length=1, max_length=700)
    suggested_replies: list[str] = Field(max_length=3)
    signals: list[Literal[tuple(ALL_SIGNALS)]]  # type: ignore[valid-type]
    done: bool


def _claude_turn(conv: Conversation, parent_text: str | None, events: list[Event], client) -> ClaudeTurn | None:
    if client is None:
        if not settings.anthropic_api_key:
            return None
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    said = [f'{e.counterparty or "unknown"}: "{e.description}"' for e in events if not is_money(e)][-6:]
    context = (f"What Lookout noticed: {describe_events(events)}.\n"
               + (f"What the sender said:\n" + "\n".join(said) + "\n" if said else "") +
               f"Signals so far: {', '.join(conv.signals) or 'none'}.\n"
               f"Turns so far: {sum(m.role == 'lookout' for m in conv.messages)} of {MAX_TURNS}.")
    opened = {"role": "user", "content": "(The check-in screen just opened. Greet them and ask your first question.)"}
    history = [opened] + [{"role": "user" if m.role == "parent" else "assistant", "content": m.text}
                          for m in conv.messages]
    try:
        # Effort low: short, conversational turns where latency matters more than depth.
        response = client.messages.parse(
            model=settings.llm_model, max_tokens=4000, system=SYSTEM_PROMPT + "\n\n" + context,
            messages=history, output_format=ClaudeTurn, output_config={"effort": "low"},
        )
        turn = response.parsed_output
        if turn is None:
            raise ValueError(f"no structured output (stop_reason={getattr(response, 'stop_reason', None)})")
        if FORBIDDEN.search(turn.reply):
            raise ValueError("reply looks like it asks for a secret")
        turn.suggested_replies = [r[:60] for r in turn.suggested_replies]
        return turn
    except (ValidationError, ValueError) as exc:
        log.warning("Conversation turn invalid, switching to the scripted interview: %s", exc)
    except Exception as exc:  # noqa: BLE001 - any API/network failure falls back
        log.warning("Conversation turn failed, switching to the scripted interview: %s", exc)
    return None


def take_turn(conv: Conversation, parent_text: str | None, events: list[Event], *,
              llm_client=None) -> list[str]:
    """Advance the conversation by one parent message (None opens it). Returns newly learned signals."""
    if conv.done:
        return []
    before = list(conv.signals)
    if parent_text is not None:
        conv.messages.append(Message(role="parent", text=parent_text.strip()[:500]))

    if conv.mode == "claude" or (parent_text is None and (llm_client or settings.anthropic_api_key)):
        turn = _claude_turn(conv, parent_text, events, llm_client)
        if turn is not None:
            conv.mode = "claude"
            if parent_text is not None:
                conv.add_signals(turn.signals)
                conv.add_signals(keyword_signals(parent_text))   # safety net under the model
            lookout_turns = sum(m.role == "lookout" for m in conv.messages)
            if turn.done or lookout_turns + 1 >= MAX_TURNS:
                _say(conv, turn.reply, [], "claude")
                conv.done, conv.step = True, None
            else:
                _say(conv, turn.reply, turn.suggested_replies, "claude")
            return [s for s in conv.signals if s not in before]
        # Claude failed: continue with the scripted interview from a sensible point.
        if conv.mode == "claude":
            conv.mode = "scripted"
            conv.step = "on_call" if set(conv.signals) & ({"someone_asked"} | SCAM_SIGNALS) else "who_asked"
            if parent_text is not None:
                conv.add_signals(keyword_signals(parent_text))
            node = SCRIPT[conv.step]
            _say(conv, node.text, node.replies, "scripted")
            return [s for s in conv.signals if s not in before]

    conv.mode = "scripted"
    scripted_turn(conv, parent_text, events)
    return [s for s in conv.signals if s not in before]
