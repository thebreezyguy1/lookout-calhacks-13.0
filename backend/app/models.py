"""Core domain models shared across the ingester, risk engine and policy."""

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EventSource(str, Enum):
    NESSIE = "nessie"
    CRS = "crs"
    INJECTOR = "injector"  # demo scenarios only


class EventKind(str, Enum):
    PURCHASE = "purchase"
    TRANSFER = "transfer"
    WITHDRAWAL = "withdrawal"
    NEW_PAYEE = "new_payee"
    CREDIT_INQUIRY = "credit_inquiry"
    NEW_CREDIT_ACCOUNT = "new_credit_account"
    IDENTITY_CHECK = "identity_check"


class Event(BaseModel):
    """A single normalized observation about the protected person."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    person_id: str
    source: EventSource
    kind: EventKind
    occurred_at: datetime = Field(default_factory=_now)
    amount: float | None = None
    merchant: str | None = None
    description: str | None = None
    raw: dict = Field(default_factory=dict)


class Tier(str, Enum):
    """Escalation tiers, lowest to highest."""

    LOG = "log"
    CHECK_IN = "check_in"
    ALERT = "alert"
    RECOMMEND_HOLD = "recommend_hold"


class Assessment(BaseModel):
    """Output of the risk engine for a window of events."""

    person_id: str
    event_ids: list[str]
    risk_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    pattern: str | None = None  # e.g. "grandparent_emergency"
    rule_hits: list[str] = Field(default_factory=list)
    reasoning: str = ""


class Decision(BaseModel):
    """What the action policy decided to do, and whether a human must approve it."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    assessment: Assessment
    tier: Tier
    requires_approval: bool
    approved: bool | None = None
    created_at: datetime = Field(default_factory=_now)
