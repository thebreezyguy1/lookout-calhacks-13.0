"""Normalize Nessie purchases, transfers and withdrawals into Lookout events."""

from datetime import datetime, timezone

from app.integrations.nessie import NessieClient
from app.models import Event, EventKind, EventSource


def _when(value: str | None) -> datetime:
    if value:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(value[:19], fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return datetime.now(timezone.utc)


def purchase_event(person_id: str, p: dict, merchant: dict | None = None) -> Event:
    merchant = merchant or {}
    category = merchant.get("category")
    return Event(
        person_id=person_id, source=EventSource.NESSIE, kind=EventKind.PURCHASE,
        occurred_at=_when(p.get("purchase_date")), amount=p.get("amount"),
        merchant=merchant.get("name") or p.get("merchant_id"),
        category=", ".join(category) if isinstance(category, list) else category,
        description=p.get("description"), raw=p,
    )


def transfer_event(person_id: str, t: dict) -> Event:
    return Event(
        person_id=person_id, source=EventSource.NESSIE, kind=EventKind.TRANSFER,
        occurred_at=_when(t.get("transaction_date")), amount=t.get("amount"),
        counterparty=t.get("payee_id"), description=t.get("description"), raw=t,
    )


def withdrawal_event(person_id: str, w: dict) -> Event:
    return Event(
        person_id=person_id, source=EventSource.NESSIE, kind=EventKind.WITHDRAWAL,
        occurred_at=_when(w.get("transaction_date")), amount=w.get("amount"),
        description=w.get("description"), raw=w,
    )


def fetch_events(person_id: str, customer_id: str, client: NessieClient | None = None) -> list[Event]:
    """Pull all activity for a Nessie customer (read-only)."""
    client = client or NessieClient()
    merchants: dict[str, dict] = {}
    events: list[Event] = []
    for account in client.accounts(customer_id):
        account_id = account["_id"]
        for p in client.purchases(account_id):
            mid = p.get("merchant_id")
            if mid and mid not in merchants:
                try:
                    merchants[mid] = client.merchant(mid)
                except Exception:  # noqa: BLE001 - merchant lookup is best-effort
                    merchants[mid] = {}
            events.append(purchase_event(person_id, p, merchants.get(mid)))
        events += [transfer_event(person_id, t) for t in client.transfers(account_id)
                   if t.get("payer_id") == account_id]
        events += [withdrawal_event(person_id, w) for w in client.withdrawals(account_id)]
    return events
