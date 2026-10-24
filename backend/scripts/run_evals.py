"""Run the labeled scenarios through the agent and report detection metrics.

Usage (from backend/):
    python -m scripts.run_evals          # rules only (offline, deterministic)
    python -m scripts.run_evals --llm    # rules + Claude (needs ANTHROPIC_API_KEY)
    python -m scripts.run_evals --set holdout   # blind scenarios written without seeing the rules

Each scenario is replayed one event at a time, the way events arrive in real life.
Metrics:
- detection rate: scams that reach their expected tier
- false-alarm rate: normal scenarios that reach ALERT or higher
- check-in rate on normal: normal scenarios that trigger a (gentle) check-in
- time to detect: minutes from the first scam event to reaching the expected tier
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from app.agent.policy import decide
from app.agent.risk import assess
from app.models import Baseline, Tier
from app.scenarios import SCENARIOS_DIR, load_scenarios, to_events

ORDER = [Tier.LOG, Tier.CHECK_IN, Tier.ALERT, Tier.RECOMMEND_HOLD]
RESULTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "results"
SETS = {"dev": SCENARIOS_DIR, "holdout": SCENARIOS_DIR.parent / "holdout"}


def rank(tier: Tier) -> int:
    return ORDER.index(tier)


def baseline_for(dormant: bool) -> Baseline:
    """Mirrors P20 Dolores Whitcomb's real CRS file when dormant; an active file otherwise."""
    if dormant:
        return Baseline(person_id="eval", persona_id="P20", name="DOLORES WHITCOMB",
                        file_status="FileReturned", total_accounts=6, open_accounts=0,
                        months_since_last_new_account=60, inquiries_last_24m=0, fraud_score=8,
                        dormant_file=True)
    return Baseline(person_id="eval", total_accounts=9, open_accounts=4,
                    months_since_last_new_account=8, inquiries_last_24m=1, score=740,
                    dormant_file=False)


def run(use_llm: bool, scenario_set: str = "dev") -> dict:
    now = datetime.now(timezone.utc)
    rows = []
    for s in load_scenarios(SETS[scenario_set]):
        window, history = to_events(s, "eval", now=now)
        baseline = baseline_for(s.dormant_file)
        expected = Tier(s.expected_min_tier) if s.expected_min_tier else Tier.ALERT
        first_hit_minutes, final = None, None
        for k in range(1, len(window) + 1):
            # LLM only on the full window to keep cost down; rules on every step.
            last = k == len(window)
            a = assess("eval", window[:k], baseline, history, use_llm=use_llm and last)
            d = decide(a)
            final = (a, d)
            if s.label == "scam" and first_hit_minutes is None and rank(d.tier) >= rank(expected):
                first_hit_minutes = (window[k - 1].occurred_at - window[0].occurred_at).total_seconds() / 60
        a, d = final
        rows.append({
            "id": s.id, "label": s.label, "expected": expected.value if s.label == "scam" else "-",
            "tier": d.tier.value, "risk": a.risk_score, "confidence": a.confidence,
            "pattern": a.pattern, "rules": a.rule_hits, "engine": a.engine,
            "detected": s.label == "scam" and first_hit_minutes is not None,
            "minutes_to_detect": first_hit_minutes,
            "false_alarm": s.label == "normal" and rank(d.tier) >= rank(Tier.ALERT),
            "check_in": s.label == "normal" and d.tier is Tier.CHECK_IN,
        })

    scams = [r for r in rows if r["label"] == "scam"]
    normals = [r for r in rows if r["label"] == "normal"]
    times = [r["minutes_to_detect"] for r in scams if r["minutes_to_detect"] is not None]
    summary = {
        "engine": "rules+llm" if use_llm else "rules",
        "set": scenario_set,
        "scenarios": len(rows),
        "detection_rate": round(sum(r["detected"] for r in scams) / len(scams), 3) if scams else None,
        "false_alarm_rate": round(sum(r["false_alarm"] for r in normals) / len(normals), 3) if normals else None,
        "normal_check_in_rate": round(sum(r["check_in"] for r in normals) / len(normals), 3) if normals else None,
        "median_minutes_to_detect": median(times) if times else None,
    }
    return {"summary": summary, "rows": rows, "ran_at": now.isoformat()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", action="store_true", help="include Claude (needs ANTHROPIC_API_KEY)")
    parser.add_argument("--set", choices=list(SETS), default="dev",
                        help="dev: scenarios written with the rules; holdout: written blind")
    args = parser.parse_args()
    result = run(args.llm, args.set)

    print(f"{'scenario':32} {'label':7} {'expected':15} {'got':15} {'risk':>5}  pattern")
    for r in result["rows"]:
        mark = ""
        if r["label"] == "scam":
            mark = "OK " if r["detected"] else "MISS"
        elif r["false_alarm"]:
            mark = "FALSE ALARM"
        print(f"{r['id']:32} {r['label']:7} {r['expected']:15} {r['tier']:15} {r['risk']:5.2f}  "
              f"{r['pattern'] or '-':24} {mark}")
    print("\nSummary:", json.dumps(result["summary"], indent=2))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"evals_{result['summary']['set']}_{result['summary']['engine']}.json"
    out.write_text(json.dumps(result, indent=2, default=str))
    print(f"Saved {out.relative_to(RESULTS_DIR.parents[1])}")


if __name__ == "__main__":
    main()
