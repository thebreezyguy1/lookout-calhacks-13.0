"""Lookout API. Read-only toward the person's money: nothing here moves funds."""

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.agent.baseline import build_baseline
from app.agent.policy import decide
from app.agent.risk import assess
from app.config import settings
from app.integrations.crs import CRSClient, CRSError, load_personas
from app.integrations.nessie_events import fetch_events
from app.models import Baseline, Decision, Event, EventKind, EventSource
from app.scenarios import load_scenario, load_scenarios, to_events
from app.store import store

app = FastAPI(
    title="Lookout",
    description="Scam-pattern detection for aging parents. Read-only; never moves money.",
    version="0.2.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/docs")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------- Baseline (real CRS data) ----------

@app.post("/people/{person_id}/baseline", response_model=Baseline)
def pull_baseline(person_id: str, persona: str = "P20", bureau: str = "experian") -> Baseline:
    """Pull the person's credit report and Fraud Finder check from the CRS sandbox."""
    personas = load_personas()
    if persona not in personas:
        raise HTTPException(404, f"Unknown persona {persona}")
    p = personas[persona]
    client = CRSClient()
    report, fraud, errors, request_ids = None, None, [], []
    try:
        r = client.credit_report(p["credit_report"][bureau]["body"], bureau)
        report, request_ids = r.body, [r.request_id] if r.request_id else []
    except CRSError as exc:
        errors.append(str(exc))
    try:
        f = client.fraud_finder(p["fraud_finder"]["body"])
        fraud = f.body
        if f.request_id:
            request_ids.append(f.request_id)
    except Exception as exc:  # noqa: BLE001 - fraud check is additive
        errors.append(f"fraud_finder: {exc}")

    baseline = build_baseline(person_id, report, fraud, persona_id=persona, name=p["name"],
                              bureau_errors=errors, request_ids=request_ids)
    store.baselines[person_id] = baseline
    store.log(person_id, "baseline_built", persona=persona, bureau=bureau,
              dormant=baseline.dormant_file, errors=errors, request_ids=request_ids)
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


@app.get("/people/{person_id}/audit")
def audit(person_id: str) -> list[dict]:
    return [r.model_dump(mode="json") for r in store.audit if r.person_id == person_id]


@app.post("/people/{person_id}/reset")
def reset(person_id: str) -> dict[str, str]:
    """Clear a person's events and decisions (demo reset). Keeps the audit log."""
    store.reset(person_id)
    return {"status": "reset"}
