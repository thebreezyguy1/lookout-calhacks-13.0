# Lookout — project context for Claude

Read `README.md` for the product and architecture. This file holds the rules every change must follow.

## What we're building

An agent that watches an aging parent's bank activity (Nessie) and credit file (CRS sandbox), detects scam *patterns* across events, and takes tiered, explainable actions. Built at Cal Hacks 13.0 for the Microsoft "Built for the Real World" track, Hyphenate (autonomous finance), CRS, Capital One Nessie, and Anthropic tracks.

## Hard rules

1. **The agent never moves money** and never calls any endpoint that creates transfers, purchases or withdrawals on the parent's behalf. The scenario injector (demo only) is the only code allowed to create purchases, and it lives behind a separate, clearly named module.
2. **Every action goes through the action policy** in `backend/app/agent/policy.py`. Nothing sends an alert or recommends a hold directly.
3. **`RECOMMEND_HOLD` always requires human approval.** No auto-approval path, not even in demo mode.
4. **Every decision writes an audit record**: observed events, rule hits, LLM reasoning summary, confidence, chosen tier.
5. **Sandbox data only.** For CRS, send only the exact persona bodies in `backend/fixtures/crs_personas.json` (P01–P20); a body that doesn't match a persona goes to the real vendor and can be billed. Use Nessie mock customers. Never put real names, SSNs or account numbers in code, fixtures, logs or prompts.
   - Our protected "parent" is **P20 Dolores Whitcomb** (74, retiree, stale file, no active accounts).
   - CRS reference (schemas, personas, error codes): the CRS MCP server, `npx -y @crscreditapi/crs-calhacks-mcp-server@latest`.
   - CRS returns HTTP 200 for errors; always check `errorMessages` (the client raises `CRSError`).
6. **No secrets in the repo.** Keys come from environment variables via `backend/app/config.py`. `.env` is gitignored.
7. **LLM output is structured.** The model returns JSON validated against Pydantic models; if validation fails, fall back to the rules-only result and log it.

## Conventions

- Backend: Python 3.11+, FastAPI, Pydantic v2, httpx. Type hints everywhere.
- Keep integration clients thin: request/response only, no business logic.
- Risk logic lives in `backend/app/agent/`; it must be testable without network access (inject clients).
- Every new rule or scam pattern gets at least one scenario in `evals/scenarios/`.
- Run `pytest` from `backend/` before committing.

## Judging priorities (keep these in mind when trading off scope)

- Working end-to-end demo beats breadth.
- Measurable: detection rate, false-alarm rate, time-to-detect from `evals/`.
- Clear story of what's real vs. simulated.
- Path to production: Nessie → Plaid, CRS sandbox → production bureau access, deploy on Azure.
