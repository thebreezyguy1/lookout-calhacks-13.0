"""Lookout API. Read-only toward the person's money: nothing here moves funds."""

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.agent.baseline import build_baseline
from app.agent.identity import identity_level, identity_signals
from app.agent.policy import decide
from app.agent.conversation import SIGNALS as CONVERSATION_SIGNALS
from app.agent.conversation import Conversation, conversation_hits, parent_answer, take_turn
from app.agent.risk import assess, assess_identity, escalate_from_conversation
from app.config import settings
from app.integrations.crs import CREDIT_REPORT_PATHS, CRSClient, CRSError, load_personas
from app.integrations.nessie_events import fetch_events
from app.models import Baseline, Decision, Event, EventKind, EventSource, ParentAnswer, Tier
from app.scenarios import load_scenario, load_scenarios, to_events
from app.store import store

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(
    title="Lookout",
    description="Scam-pattern detection for aging parents. Read-only; never moves money.",
    version="0.2.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/app/")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------- Baseline (real CRS data) ----------

@app.get("/personas")
def personas() -> list[dict]:
    """CRS sandbox test personas (synthetic people), for the dashboard's persona picker."""
    return [{"id": pid, "name": p["name"], "profile": p["profile"]} for pid, p in load_personas().items()]


@app.post("/people/{person_id}/baseline", response_model=Baseline)
def pull_baseline(person_id: str, persona: str = "P20", bureau: str = "experian") -> Baseline:
    """Pull the person's credit reports (all three bureaus) and Fraud Finder check from the CRS sandbox.

    `bureau` is the file the baseline is built from; all three feed the identity check.
    The identity result goes through the action policy like any other assessment.
    """
    all_personas = load_personas()
    if persona not in all_personas:
        raise HTTPException(404, f"Unknown persona {persona}")
    if bureau not in CREDIT_REPORT_PATHS:
        raise HTTPException(400, f"Unknown bureau {bureau}")
    p = all_personas[persona]
    client = CRSClient()
    reports: dict[str, dict] = {}
    bureau_errors: dict[str, str] = {}
    fraud, request_ids = None, []
    for b in CREDIT_REPORT_PATHS:
        try:
            r = client.credit_report(p["credit_report"][b]["body"], b)
            reports[b] = r.body
            if r.request_id:
                request_ids.append(r.request_id)
        except CRSError as exc:
            bureau_errors[b] = str(exc)
    errors = [f"{b}: {msg}" for b, msg in bureau_errors.items()]
    try:
        f = client.fraud_finder(p["fraud_finder"]["body"])
        fraud = f.body
        if f.request_id:
            request_ids.append(f.request_id)
    except Exception as exc:  # noqa: BLE001 - fraud check is additive
        errors.append(f"fraud_finder: {exc}")

    hits = identity_signals(reports, bureau_errors, fraud)
    score, level = identity_level(hits)
    primary = reports.get(bureau) or next(iter(reports.values()), None)
    baseline = build_baseline(person_id, primary, fraud, persona_id=persona, name=p["name"],
                              bureau_errors=errors, request_ids=request_ids)
    baseline = baseline.model_copy(update={"identity_score": score, "identity_level": level,
                                           "identity_signals": hits})
    store.baselines[person_id] = baseline
    store.log(person_id, "baseline_built", persona=persona, bureau=bureau,
              dormant=baseline.dormant_file, identity_level=level, identity_score=score,
              errors=errors, request_ids=request_ids)

    if hits:
        assessment = assess_identity(person_id, hits)
        decision = decide(assessment)
        store.decisions[decision.id] = decision
        store.log(person_id, "assessed", decision_id=decision.id, tier=decision.tier.value,
                  risk=assessment.risk_score, confidence=assessment.confidence,
                  pattern=assessment.pattern, rules=assessment.rule_hits, engine=assessment.engine,
                  events=0, kind="identity")
    return baseline


@app.get("/people/{person_id}/baseline", response_model=Baseline)
def get_baseline(person_id: str) -> Baseline:
    if person_id not in store.baselines:
        raise HTTPException(404, "No baseline yet. POST to this URL to pull one from CRS.")
    return store.baselines[person_id]


# ---------- Events ----------

class EventIn(BaseModel):
    kind: EventKind
    amount: float | None = None
    merchant: str | None = None
    counterparty: str | None = None
    category: str | None = None
    description: str | None = None
    occurred_at: datetime | None = None


@app.post("/people/{person_id}/events", response_model=list[Event])
def add_events(person_id: str, events: list[EventIn]) -> list[Event]:
    """Manually add events (marked as injected)."""
    created = [Event(person_id=person_id, source=EventSource.INJECTOR,
                     **e.model_dump(exclude_none=True)) for e in events]
    store.add_events(created)
    return created


@app.get("/people/{person_id}/events", response_model=list[Event])
def list_events(person_id: str) -> list[Event]:
    return sorted(store.events.get(person_id, []), key=lambda e: e.occurred_at)


@app.post("/people/{person_id}/sync-nessie", response_model=list[Event])
def sync_nessie(person_id: str, customer_id: str) -> list[Event]:
    """Read the person's activity from Nessie (read-only)."""
    events = fetch_events(person_id, customer_id)
    known = {e.raw.get("_id") for e in store.events.get(person_id, []) if e.raw.get("_id")}
    new = [e for e in events if e.raw.get("_id") not in known]
    store.add_events(new)
    store.log(person_id, "nessie_synced", customer_id=customer_id, new_events=len(new))
    return new


# ---------- Scenario injector (demo only) ----------

@app.get("/scenarios")
def scenarios() -> list[dict]:
    return [{"id": s.id, "label": s.label, "pattern": s.pattern, "notes": s.notes}
            for s in load_scenarios()]


@app.post("/people/{person_id}/inject/{scenario_id}", response_model=list[Event])
def inject(person_id: str, scenario_id: str) -> list[Event]:
    """Simulate a scenario for the demo. Events are labeled source=injector."""
    try:
        scenario = load_scenario(scenario_id)
    except KeyError:
        raise HTTPException(404, f"Unknown scenario {scenario_id}") from None
    window, history = to_events(scenario, person_id)
    store.add_events(history + window)
    store.log(person_id, "scenario_injected", scenario=scenario_id, events=len(window))
    return window


# ---------- Assess and decide ----------

@app.post("/people/{person_id}/assess", response_model=Decision)
def run_assessment(person_id: str, use_llm: bool = True) -> Decision:
    now = datetime.now(timezone.utc)
    window = store.window(person_id, settings.risk_window_hours, now)
    history = store.history(person_id, settings.risk_window_hours, now)
    assessment = assess(person_id, window, store.baselines.get(person_id), history, use_llm=use_llm)
    decision = decide(assessment)
    store.decisions[decision.id] = decision
    store.log(person_id, "assessed", decision_id=decision.id, tier=decision.tier.value,
              risk=assessment.risk_score, confidence=assessment.confidence,
              pattern=assessment.pattern, rules=assessment.rule_hits, engine=assessment.engine,
              events=len(window))
    return decision


@app.get("/people/{person_id}/decisions", response_model=list[Decision])
def list_decisions(person_id: str) -> list[Decision]:
    return sorted((d for d in store.decisions.values() if d.assessment.person_id == person_id),
                  key=lambda d: d.created_at, reverse=True)


class Review(BaseModel):
    approve: bool
    reviewer: str


@app.post("/decisions/{decision_id}/review", response_model=Decision)
def review(decision_id: str, body: Review) -> Decision:
    """A human approves or rejects a recommended hold. Lookout itself never places it."""
    decision = store.decisions.get(decision_id)
    if not decision:
        raise HTTPException(404, "Unknown decision")
    if not decision.requires_approval:
        raise HTTPException(400, "This decision does not need approval")
    decision.approved, decision.reviewed_by = body.approve, body.reviewer
    store.log(decision.assessment.person_id, "approved" if body.approve else "rejected",
              decision_id=decision_id, reviewer=body.reviewer)
    return decision


# ---------- Parent check-in ----------

@app.get("/people/{person_id}/check-in")
def pending_check_in(person_id: str) -> dict | None:
    """The newest decision the parent hasn't answered yet, in plain words. None if all is well."""
    pending = [d for d in list_decisions(person_id)
               if d.tier is not Tier.LOG and d.parent_response is None and d.conversation_of is None]
    if not pending:
        return None
    d = pending[0]
    by_id = {e.id: e for e in store.events.get(person_id, [])}
    events = sorted((by_id[i] for i in d.assessment.event_ids if i in by_id), key=lambda e: e.occurred_at)
    return {
        "decision_id": d.id,
        "message": d.assessment.parent_message,
        "events": [{"when": e.occurred_at, "kind": e.kind.value, "amount": e.amount,
                    "where": e.merchant or e.counterparty, "description": e.description}
                   for e in events],
    }


class ParentResponseIn(BaseModel):
    answer: ParentAnswer


@app.post("/decisions/{decision_id}/parent-response", response_model=Decision)
def parent_response(decision_id: str, body: ParentResponseIn) -> Decision:
    """The parent answers a check-in. Recorded for the family; it changes no tier on its own."""
    decision = store.decisions.get(decision_id)
    if not decision:
        raise HTTPException(404, "Unknown decision")
    decision.parent_response = body.answer
    decision.parent_responded_at = datetime.now(timezone.utc)
    store.log(decision.assessment.person_id, "parent_responded", decision_id=decision_id,
              answer=body.answer.value)
    return decision


class TurnIn(BaseModel):
    message: str | None = None    # None opens the conversation (idempotent)


def _conversation_view(conv: Conversation) -> dict:
    return {
        "decision_id": conv.decision_id, "mode": conv.mode, "done": conv.done,
        "messages": [m.model_dump(mode="json") for m in conv.messages],
        "suggested_replies": conv.suggested_replies, "signals": conv.signals,
        "escalations": conv.escalations,
    }


@app.get("/decisions/{decision_id}/conversation")
def get_conversation(decision_id: str) -> dict | None:
    decision = store.decisions.get(decision_id)
    if not decision:
        raise HTTPException(404, "Unknown decision")
    conv = store.conversations.get(decision.conversation_of or decision_id)
    return _conversation_view(conv) if conv else None


@app.post("/decisions/{decision_id}/conversation")
def converse(decision_id: str, body: TurnIn) -> dict:
    """One turn of the check-in conversation with the parent.

    New evidence from her answers is re-assessed and sent through the action policy as a new,
    audited decision. Her words can raise the tier, never lower it.
    """
    decision = store.decisions.get(decision_id)
    if not decision:
        raise HTTPException(404, "Unknown decision")
    origin = store.decisions.get(decision.conversation_of) if decision.conversation_of else decision
    person_id = origin.assessment.person_id
    conv = store.conversations.get(origin.id) or Conversation(decision_id=origin.id, person_id=person_id)
    if body.message is None and conv.messages:
        return _conversation_view(conv)
    if body.message is not None and not body.message.strip():
        raise HTTPException(400, "Empty message")

    by_id = {e.id: e for e in store.events.get(person_id, [])}
    events = sorted((by_id[i] for i in origin.assessment.event_ids if i in by_id), key=lambda e: e.occurred_at)
    learned = take_turn(conv, body.message, events)
    store.conversations[origin.id] = conv
    store.log(person_id, "conversation_turn", decision_id=origin.id, source=conv.messages[-1].source,
              parent=body.message, lookout=conv.messages[-1].text, learned=learned, done=conv.done)

    if any(s in CONVERSATION_SIGNALS for s in learned):
        quotes = [m.text for m in conv.messages if m.role == "parent"]
        assessment = escalate_from_conversation(origin.assessment, conversation_hits(conv.signals), quotes)
        new = decide(assessment)
        new.conversation_of = origin.id
        store.decisions[new.id] = new
        conv.escalations.append(new.id)
        store.log(person_id, "assessed", decision_id=new.id, tier=new.tier.value, risk=assessment.risk_score,
                  confidence=assessment.confidence, pattern=assessment.pattern, rules=assessment.rule_hits,
                  engine=assessment.engine, events=len(assessment.event_ids), kind="conversation")

    if conv.done:
        answer = parent_answer(conv)
        now = datetime.now(timezone.utc)
        for d in [origin, *(store.decisions[i] for i in conv.escalations)]:
            d.parent_response, d.parent_responded_at = answer, now
        store.log(person_id, "parent_responded", decision_id=origin.id, answer=answer.value if answer else None,
                  via="conversation")
    return _conversation_view(conv)


@app.get("/people/{person_id}/audit")
def audit(person_id: str) -> list[dict]:
    return [r.model_dump(mode="json") for r in store.audit if r.person_id == person_id]


@app.post("/people/{person_id}/reset")
def reset(person_id: str) -> dict[str, str]:
    """Clear a person's events and decisions (demo reset). Keeps the audit log."""
    store.reset(person_id)
    return {"status": "reset"}


# ---------- Frontend (family dashboard and parent check-in) ----------

if FRONTEND_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
