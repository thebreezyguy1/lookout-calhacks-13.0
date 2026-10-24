"""Build a person's baseline from CRS data, and turn report-to-report changes into events.

Works on the CRS Standard Format: tradelines[], inquiries[], scores[], creditFiles[].
"""

from datetime import date, datetime, timezone

from app.models import Baseline, Event, EventKind, EventSource

# A file with no new accounts and no activity for this long is "dormant":
# any new credit activity on it is highly unusual.
DORMANT_AFTER_MONTHS = 24


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m", "%m%d%Y"):
        try:
            return datetime.strptime(value[:10], fmt).date()
        except ValueError:
            continue
    return None


def _months_between(earlier: date, later: date) -> int:
    return max(0, (later.year - earlier.year) * 12 + (later.month - earlier.month))


def _to_int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def build_baseline(person_id: str, report: dict | None, fraud: dict | None = None, *,
                   persona_id: str | None = None, name: str | None = None,
                   bureau_errors: list[str] | None = None, request_ids: list[str] | None = None,
                   today: date | None = None) -> Baseline:
    today = today or datetime.now(timezone.utc).date()
    report = report or {}
    tradelines = report.get("tradelines") or []
    inquiries = report.get("inquiries") or []

    opened = [d for t in tradelines if (d := _parse_date(t.get("accountOpenedDate")))]
    activity = [d for t in tradelines
                for d in (_parse_date(t.get("lastActivityDate")), _parse_date(t.get("accountReportedDate")))
                if d]
    recent_inquiries = [i for i in inquiries
                        if (d := _parse_date(i.get("inquiryDate"))) and _months_between(d, today) <= 24]
    open_accounts = sum(1 for t in tradelines if t.get("accountStatusType") == "Open")

    months_new = _months_between(max(opened), today) if opened else None
    months_active = _months_between(max(activity), today) if activity else None

    score = next((_to_int(s.get("scoreValue")) for s in report.get("scores") or []
                  if s.get("scoreValue") is not None), None)
    files = report.get("creditFiles") or []
    detail = (files[0].get("creditFileDetail") or {}) if files else {}
    file_status = detail.get("creditFileResultStatusType") if isinstance(detail, dict) else None

    dormant = (
        bool(tradelines)
        and open_accounts == 0
        and not recent_inquiries
        and (months_new is None or months_new >= DORMANT_AFTER_MONTHS)
    )

    fraud_score = _to_int(((fraud or {}).get("risk") or {}).get("score"))

    return Baseline(
        person_id=person_id,
        persona_id=persona_id,
        name=name,
        file_status=file_status,
        total_accounts=len(tradelines),
        open_accounts=open_accounts,
        months_since_last_new_account=months_new,
        months_since_last_activity=months_active,
        inquiries_last_24m=len(recent_inquiries),
        score=score,
        fraud_score=fraud_score,
        dormant_file=dormant,
        bureau_errors=bureau_errors or [],
        request_ids=request_ids or [],
    )


def _inquiry_key(i: dict) -> tuple:
    return (i.get("creditorName"), i.get("inquiryDate"), i.get("subscriberCode"))


def _tradeline_key(t: dict) -> tuple:
    return (t.get("creditorName"), t.get("accountIdentifier"), t.get("accountOpenedDate"))


def diff_reports(person_id: str, previous: dict, current: dict) -> list[Event]:
    """New inquiries and new tradelines in `current` that weren't in `previous`.

    In production this runs on each scheduled pull. Sandbox personas never change,
    so in the demo these events come from the scenario injector instead.
    """
    events: list[Event] = []
    seen_inquiries = {_inquiry_key(i) for i in previous.get("inquiries") or []}
    for inq in current.get("inquiries") or []:
        if _inquiry_key(inq) not in seen_inquiries:
            when = _parse_date(inq.get("inquiryDate"))
            events.append(Event(
                person_id=person_id, source=EventSource.CRS, kind=EventKind.CREDIT_INQUIRY,
                occurred_at=datetime.combine(when, datetime.min.time(), timezone.utc) if when
                else datetime.now(timezone.utc),
                counterparty=inq.get("creditorName"), category=inq.get("businessType"),
                description="New hard inquiry on credit file", raw=inq,
            ))
    seen_tradelines = {_tradeline_key(t) for t in previous.get("tradelines") or []}
    for tl in current.get("tradelines") or []:
        if _tradeline_key(tl) not in seen_tradelines:
            when = _parse_date(tl.get("accountOpenedDate"))
            events.append(Event(
                person_id=person_id, source=EventSource.CRS, kind=EventKind.NEW_CREDIT_ACCOUNT,
                occurred_at=datetime.combine(when, datetime.min.time(), timezone.utc) if when
                else datetime.now(timezone.utc),
                counterparty=tl.get("creditorName"), category=tl.get("accountType"),
                amount=_to_int(tl.get("creditLimitAmount") or tl.get("highBalanceAmount")),
                description="New account opened on credit file", raw=tl,
            ))
    return events
