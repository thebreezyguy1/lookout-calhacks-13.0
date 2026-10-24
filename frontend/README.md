# Frontend

Plain HTML, CSS and JavaScript, no build step. The FastAPI backend serves this folder at `/app`, so the whole demo runs as one process.

| Page | URL | For |
|---|---|---|
| `index.html` + `dashboard.js` | http://localhost:8000/app/ | The family: current tier, risk and confidence, Claude's explanation, rule evidence with the flagged events, approval for recommended holds, Mom's answer, CRS baseline, decision history, audit log, and the demo simulation panel |
| `mom.html` + `mom.js` | http://localhost:8000/app/mom.html | The parent: one large-text question at a time and three big answers ("Yes, that was me" / "No, that wasn't me" / "Someone asked me to do this") |

Both pages poll the API every 3 seconds, so open them side by side: inject a scenario and assess on the dashboard, answer on Mom's screen, and watch her answer appear for the family.

Mom's answer is recorded and audited, but it never changes a tier or triggers an action by itself. Approving a hold only records the family's decision; Lookout never moves money or places holds.
