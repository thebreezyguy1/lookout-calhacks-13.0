"""Score all 20 CRS sandbox personas for identity risk, on real CRS responses.

Usage (from backend/):
    python -m scripts.run_persona_evals --live   # pull P01-P20 from the CRS sandbox, cache, then score
    python -m scripts.run_persona_evals          # score the cached responses (offline)

Responses are cached to fixtures/crs_<persona>_<bureau|fraud>.local.json (gitignored),
the same files `scripts.smoke_crs` writes. A bureau error is cached as
fixtures/crs_<persona>_<bureau>.error.local.json.

Only the exact persona bodies from fixtures/crs_personas.json are ever sent.
"""

import argparse
import json
from datetime import date
from pathlib import Path

from app.agent.identity import identity_level, identity_signals
from app.integrations.crs import CREDIT_REPORT_PATHS, CRSClient, CRSError, load_personas

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RESULTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "results"
BUREAUS = list(CREDIT_REPORT_PATHS)
LEVELS = ["clear", "watch", "flag"]

# Answer key, from each persona's CRS profile. Only identity misuse counts: credit
# quality (P05 subprime, P06/P07 bankruptcy, ...) and protective states (P15 freeze)
# are the person's own business and must stay clear.
EXPECTED = {
    "P12": "flag",   # identity mismatch: name and DOB don't belong to the SSN
    "P13": "flag",   # synthetic identity: inquiry burst, days-old email, someone else's phone
    "P14": "flag",   # deceased file in use ("ghosting")
    "P16": "watch",  # fraud alert on file: a past victim, watch new activity closely
}

# Fixed so results don't drift as the sandbox's canned dates age.
EVAL_DATE = date(2026, 10, 24)


def _path(persona_id: str, part: str, error: bool = False) -> Path:
    return FIXTURES / f"crs_{persona_id}_{part}{'.error' if error else ''}.local.json"


def fetch_all(personas: dict) -> None:
    client = CRSClient()
    for pid, p in personas.items():
        for bureau in BUREAUS:
            try:
                r = client.credit_report(p["credit_report"][bureau]["body"], bureau)
                _path(pid, bureau).write_text(json.dumps(r.body, indent=2))
                _path(pid, bureau, error=True).unlink(missing_ok=True)
            except CRSError as exc:
                _path(pid, bureau, error=True).write_text(json.dumps({"error": str(exc), "codes": exc.codes}))
                _path(pid, bureau).unlink(missing_ok=True)
        try:
            f = client.fraud_finder(p["fraud_finder"]["body"])
            _path(pid, "fraud").write_text(json.dumps(f.body, indent=2))
        except Exception as exc:  # noqa: BLE001 - record and keep going
            _path(pid, "fraud", error=True).write_text(json.dumps({"error": str(exc)}))
        print(f"fetched {pid}")


def load_cached(persona_id: str) -> tuple[dict[str, dict], dict[str, str], dict | None]:
    reports, errors = {}, {}
    for bureau in BUREAUS:
        if _path(persona_id, bureau).exists():
            reports[bureau] = json.loads(_path(persona_id, bureau).read_text())
        elif _path(persona_id, bureau, error=True).exists():
            errors[bureau] = json.loads(_path(persona_id, bureau, error=True).read_text())["error"]
        else:
            raise SystemExit(f"No cached {bureau} response for {persona_id}. Run with --live first.")
    fraud_file = _path(persona_id, "fraud")
    return reports, errors, json.loads(fraud_file.read_text()) if fraud_file.exists() else None


def run(personas: dict) -> dict:
    rows = []
    for pid, p in personas.items():
        reports, errors, fraud = load_cached(pid)
        hits = identity_signals(reports, errors, fraud, today=EVAL_DATE)
        score, level = identity_level(hits)
        expected = EXPECTED.get(pid, "clear")
        rows.append({"id": pid, "profile": p["profile"], "expected": expected, "got": level,
                     "score": score, "signals": [h.rule for h in hits],
                     "ok": LEVELS.index(level) >= LEVELS.index(expected) if expected != "clear"
                     else level == "clear"})

    cases = [r for r in rows if r["expected"] != "clear"]
    clean = [r for r in rows if r["expected"] == "clear"]
    summary = {
        "personas": len(rows),
        "identity_cases_caught": f"{sum(r['ok'] for r in cases)}/{len(cases)}",
        "clean_false_flags": f"{sum(r['got'] == 'flag' for r in clean)}/{len(clean)}",
        "clean_false_watches": f"{sum(r['got'] == 'watch' for r in clean)}/{len(clean)}",
    }
    return {"summary": summary, "rows": rows, "eval_date": EVAL_DATE.isoformat()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="pull fresh responses from the CRS sandbox first")
    args = parser.parse_args()
    personas = load_personas()
    if args.live:
        fetch_all(personas)

    result = run(personas)
    print(f"{'id':4} {'profile':36} {'expected':8} {'got':6} {'score':>5}  signals")
    for r in result["rows"]:
        mark = "" if r["ok"] else "  <-- MISS" if r["expected"] != "clear" else "  <-- FALSE " + r["got"].upper()
        print(f"{r['id']:4} {r['profile'][:36]:36} {r['expected']:8} {r['got']:6} {r['score']:5.2f}  "
              f"{', '.join(r['signals']) or '-'}{mark}")
    print("\nSummary:", json.dumps(result["summary"], indent=2))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / "evals_personas.json"
    out.write_text(json.dumps(result, indent=2))
    print(f"Saved {out.relative_to(RESULTS_DIR.parents[1])}")


if __name__ == "__main__":
    main()
