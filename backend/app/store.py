"""In-memory store for events, baselines, decisions and the audit log.

Good enough for the demo (single process). The interface is small on purpose so
it can be swapped for Postgres without touching the agent.
"""

from datetime import datetime, timedelta, timezone
from threading import Lock

from pydantic import BaseModel, Field

from app.models import Baseline, Decision, Event


class AuditRecord(BaseModel):
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    person_id: str
    action: str           # e.g. "baseline_built", "assessed", "approved"
    detail: dict = Field(default_factory=dict)


class Store:
    def __init__(self) -> None:
        self._lock = Lock()
        self.events: dict[str, list[Event]] = {}
        self.baselines: dict[str, Baseline] = {}
        self.decisions: dict[str, Decision] = {}
        self.conversations: dict = {}   # check-in decision id -> agent.conversation.Conversation
        self.audit: list[AuditRecord] = []

    def add_events(self, events: list[Event]) -> None:
        with self._lock:
            for e in events:
                self.events.setdefault(e.person_id, []).append(e)

    def window(self, person_id: str, hours: int, now: datetime | None = None) -> list[Event]:
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=hours)
        return [e for e in self.events.get(person_id, []) if e.occurred_at >= cutoff]

    def history(self, person_id: str, hours: int, now: datetime | None = None) -> list[Event]:
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=hours)
        return [e for e in self.events.get(person_id, []) if e.occurred_at < cutoff]

    def log(self, person_id: str, action: str, **detail) -> AuditRecord:
        record = AuditRecord(person_id=person_id, action=action, detail=detail)
        with self._lock:
            self.audit.append(record)
        return record

    def reset(self, person_id: str | None = None) -> None:
        with self._lock:
            if person_id is None:
                self.__init__()
                return
            self.events.pop(person_id, None)  # baseline is kept: it's real CRS data
            self.decisions = {k: d for k, d in self.decisions.items()
                              if d.assessment.person_id != person_id}
            self.conversations = {k: c for k, c in self.conversations.items()
                                  if c.person_id != person_id}


store = Store()
