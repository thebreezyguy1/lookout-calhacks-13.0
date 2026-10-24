"""Risk engine: fast rules plus LLM pattern reasoning over a window of events.

Planned structure:
  1. `rule_signals(events)`  -> cheap, deterministic hits (velocity, gift cards,
     new payee, first-time merchant category, new credit inquiry).
  2. `llm_assess(events, hits)` -> structured JSON (pattern, risk, confidence,
     reasoning) validated against `Assessment`; falls back to rules on failure.
  3. `assess(events)` -> combines both into one `Assessment`.
"""
