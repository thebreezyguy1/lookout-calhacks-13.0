"""CRS sandbox smoke test: log in, pull a credit report for a test identity,
and print the shape of the response so we can build the diffing logic.

Usage (from backend/):
    python -m scripts.smoke_crs                     # uses fixtures/crs_test_identity.json
    python -m scripts.smoke_crs path/to/identity.json

Never prints the access token or your password.
"""

import json
import sys
from pathlib import Path

from app.integrations.crs import CRSClient

DEFAULT_IDENTITY = Path(__file__).resolve().parents[1] / "fixtures" / "crs_test_identity.json"
INTERESTING = ("inquir", "tradeline", "account", "score", "publicrecord", "fraud", "alert")


def find_keys(node, path="", depth=0, out=None):
    """Collect paths of keys that look relevant, with list lengths."""
    out = out if out is not None else []
    if depth > 6:
        return out
    if isinstance(node, dict):
        for key, value in node.items():
            p = f"{path}.{key}" if path else key
            if any(word in key.lower() for word in INTERESTING):
                size = f" (list of {len(value)})" if isinstance(value, list) else ""
                out.append(p + size)
            find_keys(value, p, depth + 1, out)
    elif isinstance(node, list) and node:
        find_keys(node[0], f"{path}[0]", depth + 1, out)
    return out


def main() -> None:
    identity_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_IDENTITY
    identity = json.loads(identity_path.read_text())

    client = CRSClient()
    print("1. Logging in...")
    client.login()
    print("   OK, got an access token.")

    print(f"2. Pulling credit report for {identity['firstName']} {identity['lastName']}...")
    report = client.credit_report(identity)
    print(f"   OK. Top-level keys: {list(report)[:15]}")

    print("3. Fields that look useful for scam detection:")
    for path in find_keys(report)[:40]:
        print(f"   - {path}")

    out = Path(__file__).resolve().parents[1] / "fixtures" / "crs_report_sample.local.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"4. Full response saved to {out.name} (gitignored). Share its structure, not the file.")


if __name__ == "__main__":
    main()
