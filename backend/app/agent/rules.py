"""Deterministic scam signals over a window of events.

Each rule returns a RuleHit with a weight in [0, 1]. Weights combine with a
noisy-OR (1 - prod(1 - w)), so several weak signals add up and one strong
signal is enough on its own. Rules never see the network and are cheap to run
on every new event; the LLM layer adds judgment on top.
"""

import re
from collections import defaultdict
from datetime import timedelta
from statistics import median

from app.models import Baseline, Event, EventKind, RuleHit

GIFT_CARD = re.compile(r"gift\s*card|itunes|google\s*play|steam\s*card|apple\s*card|vanilla|prepaid\s*card",
                       re.IGNORECASE)
CRYPTO_OR_WIRE = re.compile(r"bitcoin|crypto|btc\s*atm|coinflip|coinme|bitcoin\s*depot|wire\s*transfer|"
                            r"western\s*union|moneygram|ria\s*money", re.IGNORECASE)
REMOTE_ACCESS = re.compile(r"anydesk|teamviewer|tech\s*support|geek\s*squad\s*renewal|norton\s*renewal|"
                           r"microsoft\s*support", re.IGNORECASE)
FAMILY_EMERGENCY = re.compile(r"bail|jail|lawyer|attorney|hospital|emergency|accident|medical|stranded|"
                              r"flight\s*home", re.IGNORECASE)
AUTHORITY = re.compile(r"\birs\b|warrant|arrest|police|sheriff|social\s*security|medicare|customs|"
                       r"court\s*fine|tax\s*debt", re.IGNORECASE)
PRIZE = re.compile(r"prize|sweepstakes|lottery|winnings|fee\s*to\s*release|processing\s*fee", re.IGNORECASE)

MONEY_KINDS = {EventKind.PURCHASE, EventKind.TRANSFER, EventKind.WITHDRAWAL}
CREDIT_KINDS = {EventKind.CREDIT_INQUIRY, EventKind.NEW_CREDIT_ACCOUNT}


def _text(e: Event) -> str:
    return " ".join(filter(None, [e.merchant, e.counterparty, e.category, e.description]))


def _within(events: list[Event], hours: float) -> list[list[Event]]:
    """All maximal groups of events that fall inside a sliding window of `hours`."""
    ordered = sorted(events, key=lambda e: e.occurred_at)
    groups, start = [], 0
    for end in range(len(ordered)):
        while ordered[end].occurred_at - ordered[start].occurred_at > timedelta(hours=hours):
            start += 1
        groups.append(ordered[start:end + 1])
    return groups


def rule_signals(window: list[Event], baseline: Baseline | None = None,
                 history: list[Event] | None = None) -> list[RuleHit]:
    """Evaluate all rules on `window` (recent events). `history` is older, known-normal activity."""
    hits: list[RuleHit] = []
    history = history or []
    money = [e for e in window if e.kind in MONEY_KINDS]

    # 1. Gift cards: the single most common payment method scammers demand.
    gift = [e for e in money if GIFT_CARD.search(_text(e))]
    if gift:
        total = sum(e.amount or 0 for e in gift)
        stores = {e.merchant for e in gift if e.merchant}
        weight = 0.35 if total < 200 else 0.55 if total < 500 else 0.7
        hits.append(RuleHit(rule="gift_card_purchases", weight=weight, event_ids=[e.id for e in gift],
                            detail=f"${total:,.0f} in gift cards across {len(stores) or 1} store(s)"))
        # Report the 24h group with the most distinct stores, not the first one that reaches two.
        group = max(_within(gift, hours=24), key=lambda g: len({e.merchant for e in g}))
        if len({e.merchant for e in group}) >= 2:
            hits.append(RuleHit(rule="gift_cards_multiple_stores_24h", weight=0.6,
                                event_ids=[e.id for e in group],
                                detail=f"Gift cards bought at {len({e.merchant for e in group})} "
                                       f"different stores within 24 hours"))

    # 2. Crypto ATMs and wire services: irreversible, favored by scammers.
    crypto = [e for e in money if CRYPTO_OR_WIRE.search(_text(e))]
    if crypto:
        total = sum(e.amount or 0 for e in crypto)
        hits.append(RuleHit(rule="crypto_or_wire", weight=0.55 if total < 1000 else 0.75,
                            event_ids=[e.id for e in crypto],
                            detail=f"${total:,.0f} sent via crypto ATM or wire service"))

    # 3. Tech-support scam fingerprints.
    remote = [e for e in window if REMOTE_ACCESS.search(_text(e))]
    if remote:
        hits.append(RuleHit(rule="remote_access_or_tech_support", weight=0.5,
                            event_ids=[e.id for e in remote],
                            detail="Payment to a remote-access or 'tech support' service"))

    # 4. Pressure language in memos: the story the scammer told.
    for rule, pattern, detail in (
        ("urgency_family_emergency", FAMILY_EMERGENCY,
         "Memo mentions a family emergency (bail, hospital, stranded)"),
        ("urgency_authority", AUTHORITY,
         "Memo mentions the IRS, police, a warrant or a government agency"),
        ("prize_fee", PRIZE, "Memo mentions a prize, sweepstakes or a fee to release winnings"),
    ):
        matched = [e for e in window if e.description and pattern.search(e.description)]
        if matched:
            hits.append(RuleHit(rule=rule, weight=0.4, event_ids=[e.id for e in matched], detail=detail))

    # 5. New payee receiving money.
    known_payees = {e.counterparty for e in history if e.counterparty}
    new_payees = {e.counterparty for e in window if e.kind is EventKind.NEW_PAYEE and e.counterparty}
    to_new = [e for e in window if e.kind is EventKind.TRANSFER and e.counterparty
              and (e.counterparty in new_payees or (history and e.counterparty not in known_payees))]
    if to_new:
        total = sum(e.amount or 0 for e in to_new)
        hits.append(RuleHit(rule="transfer_to_new_payee", weight=0.3 if total < 500 else 0.55,
                            event_ids=[e.id for e in to_new],
                            detail=f"${total:,.0f} transferred to a payee added recently"))

    # 6. Large cash withdrawals (cash couriers, "keep this secret").
    cash = [e for e in window if e.kind is EventKind.WITHDRAWAL and (e.amount or 0) >= 1000]
    if cash:
        total = sum(e.amount or 0 for e in cash)
        hits.append(RuleHit(rule="large_cash_withdrawal", weight=0.45 if total < 5000 else 0.6,
                            event_ids=[e.id for e in cash],
                            detail=f"${total:,.0f} withdrawn in cash"))

    # 7. Unusually large compared to this person's normal spending.
    typical_amounts = [e.amount for e in history if e.amount and e.kind in MONEY_KINDS]
    if len(typical_amounts) >= 5:
        typical = median(typical_amounts)
        big = [e for e in money if (e.amount or 0) >= max(5 * typical, 300)]
        if big:
            hits.append(RuleHit(rule="unusual_amount", weight=0.35, event_ids=[e.id for e in big],
                                detail=f"{len(big)} transaction(s) over 5x the usual ${typical:,.0f}"))

    # 8. Velocity: a burst of money movement.
    for group in _within(money, hours=6):
        if len(group) >= 4:
            hits.append(RuleHit(rule="velocity_burst", weight=0.3, event_ids=[e.id for e in group],
                                detail=f"{len(group)} payments within 6 hours"))
            break

    # 9. New credit activity: weighed against the CRS baseline.
    credit = [e for e in window if e.kind in CREDIT_KINDS]
    if credit:
        dormant = bool(baseline and baseline.dormant_file)
        if dormant:
            hits.append(RuleHit(
                rule="new_credit_on_dormant_file", weight=0.75, event_ids=[e.id for e in credit],
                detail=f"{len(credit)} new credit inquiry/account on a file with no new accounts in "
                       f"{baseline.months_since_last_new_account or 'many'} months and no open accounts"))
        else:
            hits.append(RuleHit(rule="new_credit_activity", weight=0.35 if len(credit) == 1 else 0.5,
                                event_ids=[e.id for e in credit],
                                detail=f"{len(credit)} new credit inquiry/account"))

    # 10. Identity signals from CRS.
    if baseline and any("deceased" in err.lower() or "E1200" in err for err in baseline.bureau_errors):
        hits.append(RuleHit(rule="deceased_identity_in_use", weight=0.9,
                            event_ids=[e.id for e in window],
                            detail="Bureau reports this identity as deceased, but it is being used"))
    risky_checks = [e for e in window if e.kind is EventKind.IDENTITY_CHECK and (e.amount or 0) >= 70]
    if risky_checks:
        hits.append(RuleHit(rule="high_fraud_finder_score", weight=0.6,
                            event_ids=[e.id for e in risky_checks],
                            detail="Contact details used with this identity score high-risk on Fraud Finder"))

    return hits


def combine(hits: list[RuleHit]) -> float:
    remaining = 1.0
    for hit in hits:
        remaining *= 1.0 - hit.weight
    return round(1.0 - remaining, 4)


IDENTITY_RULES = {"new_credit_on_dormant_file", "new_credit_activity", "deceased_identity_in_use",
                  "high_fraud_finder_score"}
GIFT_RULES = {"gift_card_purchases", "gift_cards_multiple_stores_24h"}


def guess_pattern(hits: list[RuleHit]) -> str | None:
    """Best-guess scam pattern from rule names. Order matters: most specific first."""
    names = {h.rule for h in hits}
    if not names:
        return None
    if names & IDENTITY_RULES:
        return "identity_theft"
    if "remote_access_or_tech_support" in names:
        return "tech_support_scam"
    if "urgency_authority" in names:
        return "government_impersonation"
    if "prize_fee" in names:
        return "prize_scam"
    if "urgency_family_emergency" in names:
        return "grandparent_emergency" if names & GIFT_RULES else "romance_scam" \
            if "transfer_to_new_payee" in names else "grandparent_emergency"
    if names & GIFT_RULES:
        return "gift_card_scam"
    if "crypto_or_wire" in names:
        return "crypto_or_wire_scam"
    if names & {"transfer_to_new_payee", "large_cash_withdrawal"}:
        return "payee_scam"
    return "unusual_activity"


def rules_confidence(hits: list[RuleHit]) -> float:
    """More independent signals pointing the same way means more confidence."""
    if not hits:
        return 0.9  # confident that nothing is wrong
    return round(min(0.95, 0.55 + 0.1 * len({h.rule for h in hits})), 2)
