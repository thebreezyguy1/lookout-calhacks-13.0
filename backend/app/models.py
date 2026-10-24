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
    merchant: str | None = None        # purchases
    counterparty: str | None = None    # transfers, payees, creditors
    category: str | None = None        # merchant category or creditor business type
    description: str | None = None
    raw: dict = Field(default_factory=dict)


class Baseline(BaseModel):
    """What 'normal' looks like for the protected person, built from real CRS data."""

    person_id: str
    persona_id: str | None = None
    name: str | None = None
    file_status: str | None = None
    total_accounts: int = 0
    open_accounts: int = 0
    months_since_last_new_account: int | None = None
    months_since_last_activity: int | None = None
    inquiries_last_24m: int = 0
    score: int | None = None
    fraud_score: int | None = None
    dormant_file: bool = False
    bureau_errors: list[str] = Field(default_factory=list)
    request_ids: list[str] = Field(default_factory=list)
    built_at: datetime = Field(default_factory=_now)


class Tier(str, Enum):
    """Escalation tiers, lowest to highest."""

    LOG = "log"
    CHECK_IN = "check_in"
    ALERT = "alert"
    RECOMMEND_HOLD = "recommend_hold"


class RuleHit(BaseModel):
    rule: str
    weight: float = Field(ge=0.0, le=1.0)
    event_ids: list[str] = Field(default_factory=list)
    detail: str


class Assessment(BaseModel):
    """Output of the risk engine for a window of events."""

    person_id: str
    event_ids: list[str]
    risk_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    pattern: str | None = None  # e.g. "grandparent_emergency"
    rule_hits: list[str] = Field(default_factory=list)
    signals: list[RuleHit] = Field(default_factory=list)  # full rule evidence, for the dashboard
    reasoning: str = ""
    family_summary: str = ""    # plain-language explanation for the trusted contact
    parent_message: str = ""    # gentle check-in text for the parent
    engine: str = "rules"       # "rules" or "rules+llm"


class Decision(BaseModel):
    """What the action policy decided to do, and whether a human must approve it."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    assessment: Assessment
    tier: Tier
    requires_approval: bool
    approved: bool | None = None
    reviewed_by: str | None = None
    parent_response: "ParentAnswer | None" = None
    parent_responded_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)


class ParentAnswer(str, Enum):
    """The parent's answer to a check-in. Input to the family, never an action by itself."""

    WAS_ME = "was_me"
    NOT_ME = "not_me"
    SOMEONE_ASKED = "someone_asked"


Decision.model_rebuild()
