"""Identity-risk signals from a person's CRS credit files and Fraud Finder check.

The transaction rules (rules.py) ask "is this money moving like a scam?". This module
asks the other question Lookout cares about: "is someone using this person's identity?"
It reads the raw CRS Standard Format reports (one per bureau) and the Fraud Finder body,
and returns RuleHits that combine with the same noisy-OR as the transaction rules.

Credit quality is deliberately not a signal. A low score, collections or a bankruptcy
are the person's own history, not fraud, and alarming on them would erode trust.
"""

from datetime import date, datetime, timedelta, timezone

from app.agent.baseline import _parse_date
from app.agent.rules import combine
from app.models import RuleHit

FLAG_AT = 0.7    # act: alert the family
WATCH_AT = 0.4   # note it on the dashboard, watch new activity more closely

INQUIRY_BURST_COUNT = 5
INQUIRY_BURST_DAYS = 60
NEW_EMAIL_DAYS = 90


def _inquiry_dates(reports: dict[str, dict]) -> list[date]:
    """Inquiry dates from the bureau with the most inquiries (bureaus repeat each other)."""
    per_bureau = [[d for i in r.get("inquiries") or [] if (d := _parse_date(i.get("inquiryDate")))]
                  for r in reports.values()]
    return sorted(max(per_bureau, key=len, default=[]))


def _max_in_window(dates: list[date], days: int) -> int:
    best, start = 0, 0
    for end in range(len(dates)):
        while dates[end] - dates[start] > timedelta(days=days):
            start += 1
        best = max(best, end - start + 1)
    return best


def _fraud_alert(reports: dict[str, dict]) -> str | None:
    for bureau, r in reports.items():
        for alert in r.get("responseAlertMessages") or []:
            if (alert.get("responseAlertMessageCategoryType") or "").startswith("FACTAFraudVictim"):
                return bureau
        for f in r.get("creditFiles") or []:
            if any("FRAUD ALERT" in (c.get("commentText") or "").upper() for c in f.get("comments") or []):
                return bureau
    return None


def identity_signals(reports: dict[str, dict], bureau_errors: dict[str, str] | None = None,
                     fraud: dict | None = None, today: date | None = None) -> list[RuleHit]:
    """`reports` maps bureau -> credit report body; `bureau_errors` maps bureau -> error text."""
    today = today or datetime.now(timezone.utc).date()
    bureau_errors = bureau_errors or {}
    hits: list[RuleHit] = []

    # 1. Bureau says this identity belongs to someone who has died.
    deceased = [b for b, err in bureau_errors.items() if "E1200" in err or "deceased" in err.lower()]
    if deceased:
        hits.append(RuleHit(rule="deceased_identity", weight=0.9,
                            detail=f"{len(deceased)} bureau(s) report this identity as deceased"))

    # 2. A fraud alert is on file: this person has already been a fraud victim.
    if bureau := _fraud_alert(reports):
        hits.append(RuleHit(rule="fraud_alert_on_file", weight=0.5,
                            detail=f"Initial fraud alert on the {bureau.title()} file"))

    # 3. Many hard inquiries in a short burst: someone applying for credit in a hurry.
    dates = [d for d in _inquiry_dates(reports) if (today - d).days <= 730]
    burst = _max_in_window(dates, INQUIRY_BURST_DAYS)
    if burst >= INQUIRY_BURST_COUNT:
        hits.append(RuleHit(rule="inquiry_burst", weight=0.5,
                            detail=f"{burst} hard inquiries within {INQUIRY_BURST_DAYS} days"))

    # 4. Fraud Finder: contact details that don't belong to this person.
    fraud = fraud or {}
    risk = fraud.get("risk") or {}
    score = risk.get("score")
    if isinstance(score, (int, float)):
        if score >= 70:
            hits.append(RuleHit(rule="fraud_finder_high", weight=0.6,
                                detail=f"Fraud Finder risk score {score:.0f}/100 (high)"))
        elif score >= 40:
            hits.append(RuleHit(rule="fraud_finder_medium", weight=0.3,
                                detail=f"Fraud Finder risk score {score:.0f}/100 (medium)"))

    phone = risk.get("phone") or {}
    if (phone.get("owner") or {}).get("last_name_match") == "no_match":
        hits.append(RuleHit(rule="phone_owner_mismatch", weight=0.35,
                            detail="The phone number is registered to someone else"))
    if phone.get("line_type") == "voip" or phone.get("prepaid"):
        hits.append(RuleHit(rule="disposable_phone", weight=0.15, detail="Prepaid or VoIP phone number"))

    first_seen = _parse_date((fraud.get("eam") or {}).get("date_first_seen"))
    if first_seen and (today - first_seen).days <= NEW_EMAIL_DAYS:
        hits.append(RuleHit(rule="new_email", weight=0.35,
                            detail=f"Email address first seen {(today - first_seen).days} days ago"))

    email_status = (fraud.get("email_validation") or {}).get("status")
    if email_status == "invalid" or phone.get("status") == "dead":
        hits.append(RuleHit(rule="dead_contact_details", weight=0.25,
                            detail="Email is invalid or phone is disconnected"))

    if ">" in ((risk.get("postal") or {}).get("ip_postal_distance") or ""):
        hits.append(RuleHit(rule="ip_far_from_address", weight=0.2,
                            detail="Connecting from over 1,000 miles away from the home address"))

    return hits


def identity_level(hits: list[RuleHit]) -> tuple[float, str]:
    """Combined score and level: 'flag', 'watch' or 'clear'."""
    score = combine(hits)
    return score, "flag" if score >= FLAG_AT else "watch" if score >= WATCH_AT else "clear"
