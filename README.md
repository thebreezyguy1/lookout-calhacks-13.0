# Lookout

**An AI agent that protects aging parents from financial scams — and knows when to ask a human.**

Banks review transactions one at a time. Scams unfold as a *pattern*: a panicked phone call, gift cards bought at three stores in one afternoon, a new payee, a credit inquiry from a lender nobody contacted. Lookout watches a parent's accounts and credit file (read-only, with their consent), connects those dots, and responds in proportion: a gentle check-in with the parent, an alert to a trusted family member, or a recommended hold that a human must approve. **Lookout never moves money.**

Built at Cal Hacks 13.0.

## Who it's for

Adult children of aging parents — and the parents themselves, who stay in control and can see everything Lookout sees.

## How it works

```
Nessie (accounts, purchases, transfers) ─┐
CRS sandbox (credit report, identity)  ──┼─► Ingester ─► Event store
Scenario injector (demo / judges)      ─┘        │
                                                 ▼
                                   Risk engine
                                   ├─ fast rules (velocity, new payee, amounts)
                                   └─ LLM pattern reasoning over a 72h window
                                                 │
                                   Action policy (tiered, human-in-the-loop)
                                   ├─ LOG       → audit trail only
                                   ├─ CHECK_IN  → plain-language note to the parent
                                   ├─ ALERT     → trusted contact, with evidence
                                   └─ RECOMMEND_HOLD → requires human approval
                                                 │
                                   Audit log + family dashboard
```

Credit monitoring works by pulling the parent's CRS credit report on a schedule and diffing it against the previous pull, surfacing new hard inquiries and newly opened accounts.

## Repo layout

| Path | What lives there |
|---|---|
| `backend/` | FastAPI service: integrations, risk engine, action policy, audit log |
| `backend/app/integrations/` | Thin clients for Nessie and the CRS sandbox |
| `backend/app/agent/` | Risk engine and action policy |
| `frontend/` | Family dashboard and parent check-in view (to be scaffolded) |
| `evals/` | Labeled scam / normal-behavior scenarios and the eval runner |
| `docs/` | Architecture notes and prize-track checklist |

## Running locally

```bash
cp .env.example .env          # fill in your keys — never commit .env
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload # http://localhost:8000/health
pytest
```

## Responsible design

- **Consent and dignity:** the parent opts in and can see every signal, alert and decision.
- **Read-only:** Lookout cannot move money. Holds and freezes are recommendations a human approves.
- **Explainable:** every action records what was observed, why, and how confident the agent was.
- **Sandbox data only:** this project uses Nessie mock data and CRS-provided test identities — never real people's data.

## Data sources

- [Capital One Nessie](http://api.nessieisreal.com/) — mock banking data
- [CRS Credit API sandbox](https://calhacks.crscreditapi.com) — credit and identity data for test identities
