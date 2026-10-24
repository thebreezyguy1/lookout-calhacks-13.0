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
| `frontend/` | Family dashboard and parent check-in view (static, served at `/app`) |
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

## Demo flow (API)

With the server running, open the family dashboard at http://localhost:8000/app/ and Mom's check-in screen at http://localhost:8000/app/mom.html side by side. The API docs are at http://localhost:8000/docs. Or use curl:

```bash
# 1. Real CRS data: build Mom's baseline from persona P20 (Dolores Whitcomb)
curl -X POST "localhost:8000/people/mom/baseline?persona=P20"
# 2. Simulate a scam (events are labeled source=injector)
curl -X POST localhost:8000/people/mom/inject/scam-grandparent-bail
# 3. Assess: rules + Claude, then the action policy picks a tier
curl -X POST localhost:8000/people/mom/assess
# 4. A family member approves or rejects a recommended hold
curl -X POST localhost:8000/decisions/<decision_id>/review \
     -H 'Content-Type: application/json' -d '{"approve": true, "reviewer": "daughter"}'
# 5. Audit trail
curl localhost:8000/people/mom/audit
```

**What's real vs. simulated:** the baseline comes from live CRS sandbox calls (credit report + Fraud Finder). CRS personas never change, so new suspicious activity comes from the scenario injector, and every injected event is labeled as such.

## Evals

```bash
cd backend
python -m scripts.run_evals                  # dev set, rules only, offline
python -m scripts.run_evals --set holdout    # blind set, written without seeing the rules
python -m scripts.run_evals --llm            # rules + Claude (needs ANTHROPIC_API_KEY)
python -m scripts.run_persona_evals          # identity check on all 20 CRS personas (--live to re-pull)
```

Reports detection rate, false-alarm rate, normal check-in rate and median time-to-detect.

| Eval | Caught | False alarms |
|---|---|---|
| Dev set (`evals/scenarios/`, 8 scam / 6 normal, written with the rules) | 8/8 | 0/6 |
| **Blind holdout** (`evals/holdout/`, 8 scam / 8 normal, not tuned against) | **7/8** | **2/8** |
| Identity check on real CRS responses (20 personas) | 4/4 identity cases | 0/16 clean |

Rules only. The holdout misses need context (a known payee, an active credit file, a merchant name with no "gift card" wording), which is what the Claude layer is for.

## Deploy to Azure

See [docs/deploy-azure.md](docs/deploy-azure.md): one script, `infra/azure/deploy.sh`, builds the image in Azure Container Registry and runs it on Azure Container Apps.

## Presentation

[docs/presentation.html](docs/presentation.html) is the pitch brief: story, real vs. simulated, results, 5-minute demo script and path to production.

## Responsible design

- **Consent and dignity:** the parent opts in and can see every signal, alert and decision.
- **Read-only:** Lookout cannot move money. Holds and freezes are recommendations a human approves.
- **Explainable:** every action records what was observed, why, and how confident the agent was.
- **Sandbox data only:** this project uses Nessie mock data and CRS-provided test identities — never real people's data.

## Data sources

- [Capital One Nessie](http://api.nessieisreal.com/) — mock banking data
- [CRS Credit API sandbox](https://calhacks.crscreditapi.com) — credit and identity data for test identities
