"""CRS sandbox smoke test using the official sandbox personas.

Usage (from backend/):
    python -m scripts.smoke_crs              # P20 Dolores Whitcomb, Experian
    python -m scripts.smoke_crs P13          # any persona id P01-P20
    python -m scripts.smoke_crs P20 transunion

Pulls a credit report and a Fraud Finder check, prints a summary, and saves the
raw responses to fixtures/*.local.json (gitignored). Never prints your token.
"""

import json
import sys
from pathlib import Path

from app.integrations.crs import CRSClient, CRSError, load_personas

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def summarize_report(body: dict) -> None:
    files = body.get("creditFiles") or []
    status = [f.get("creditFileDetail") for f in files]
    tradelines = body.get("tradelines") or []
    inquiries = body.get("inquiries") or []
    scores = body.get("scores") or []
    print(f"   file status: {status}")
    print(f"   tradelines: {len(tradelines)}  inquiries: {len(inquiries)}  "
          f"public records: {len(body.get('publicRecords') or [])}")
    for s in scores:
        print(f"   score: {s.get('modelName')} = {s.get('scoreValue')} "
              f"(no-score reason: {s.get('noScoreReasonType')})")
    if tradelines:
        print(f"   tradeline fields: {sorted(tradelines[0])}")
    if inquiries:
        print(f"   inquiry fields: {sorted(inquiries[0])}")


def main() -> None:
    persona_id = sys.argv[1] if len(sys.argv) > 1 else "P20"
    bureau = sys.argv[2] if len(sys.argv) > 2 else "experian"
    persona = load_personas()[persona_id]
    print(f"Persona {persona_id}: {persona['name']} ({persona['profile']})")
    print(f"Expected: {persona['credit_report'][bureau]['expected']}\n")

    client = CRSClient()
    print("1. Logging in...")
    client.login()
    print("   OK, got an access token.")

    print(f"2. Credit report ({bureau})...")
    try:
        report = client.credit_report(persona["credit_report"][bureau]["body"], bureau)
        print(f"   OK. RequestID: {report.request_id}")
        summarize_report(report.body)
        (FIXTURES / f"crs_{persona_id}_{bureau}.local.json").write_text(json.dumps(report.body, indent=2))
    except CRSError as e:
        print(f"   Bureau returned an error: {e}")

    print("3. Fraud Finder...")
    try:
        fraud = client.fraud_finder(persona["fraud_finder"]["body"])
        risk = fraud.body.get("risk") or {}
        print(f"   OK. RequestID: {fraud.request_id}")
        print(f"   risk.score: {risk.get('score')}  (expected: {persona['fraud_finder']['expected']})")
        (FIXTURES / f"crs_{persona_id}_fraud.local.json").write_text(json.dumps(fraud.body, indent=2))
    except (CRSError, Exception) as e:  # noqa: BLE001 - smoke test, show anything
        print(f"   Fraud Finder failed: {e}")

    print("\nRaw responses saved to fixtures/*.local.json (gitignored).")


if __name__ == "__main__":
    main()
