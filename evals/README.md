# Evals

Labeled scenarios that measure whether Lookout catches scams without crying wolf.

Each file in `scenarios/` is one timeline of events for a protected person, plus the expected outcome:

```json
{
  "id": "grandparent-emergency-01",
  "label": "scam",
  "pattern": "grandparent_emergency",
  "expected_min_tier": "alert",
  "events": [
    {"kind": "purchase", "merchant": "Target", "amount": 500, "description": "Gift cards", "offset_minutes": 0}
  ]
}
```

`label` is `scam` or `normal`. Normal scenarios matter as much as scams: they measure the false-alarm rate.

Metrics reported by the runner: detection rate, false-alarm rate, median time-to-detect.
