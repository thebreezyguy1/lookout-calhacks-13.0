"""Load labeled scenarios (evals/scenarios/*.json) and turn them into timestamped events.

Used by the eval runner and by the demo scenario injector. Scenario event times are
offsets in minutes relative to the end of the scenario, so they always land inside
the current risk window. `history` events are older, normal activity (days ago).
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from app.models import Event, EventKind, EventSource

SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "evals" / "scenarios"


class ScenarioEvent(BaseModel):
    kind: EventKind
    offset_minutes: int = 0            # minutes after the first event
    amount: float | None = None
    merchant: str | None = None
    counterparty: str | None = None
    category: str | None = None
    description: str | None = None


class Scenario(BaseModel):
    id: str
    label: str                          # "scam" or "normal"
    pattern: str | None = None
    expected_min_tier: str | None = None
    notes: str = ""
    dormant_file: bool = True           # default: our parent persona, P20 Dolores
    history: list[ScenarioEvent] = Field(default_factory=list)  # offsets in days before now
    events: list[ScenarioEvent]


def load_scenarios(directory: Path = SCENARIOS_DIR) -> list[Scenario]:
    return [Scenario.model_validate_json(p.read_text()) for p in sorted(directory.glob("*.json"))]


def load_scenario(scenario_id: str, directory: Path = SCENARIOS_DIR) -> Scenario:
    for s in load_scenarios(directory):
        if s.id == scenario_id:
            return s
    raise KeyError(scenario_id)


def to_events(scenario: Scenario, person_id: str, now: datetime | None = None,
              source: EventSource = EventSource.INJECTOR) -> tuple[list[Event], list[Event]]:
    """Returns (window_events, history_events). Window events end at `now`."""
    now = now or datetime.now(timezone.utc)
    span = max((e.offset_minutes for e in scenario.events), default=0)
    start = now - timedelta(minutes=span)

    def make(se: ScenarioEvent, at: datetime) -> Event:
        return Event(person_id=person_id, source=source, kind=se.kind, occurred_at=at,
                     amount=se.amount, merchant=se.merchant, counterparty=se.counterparty,
                     category=se.category, description=se.description,
                     raw={"scenario": scenario.id})

    window = [make(se, start + timedelta(minutes=se.offset_minutes)) for se in scenario.events]
    history = [make(se, now - timedelta(days=max(4, se.offset_minutes))) for se in scenario.history]
    return window, history
