# Deploying Lookout to Azure

Lookout runs as one container on **Azure Container Apps**. The image is built in the cloud by Azure Container Registry, so you don't need Docker locally.

## Prerequisites

- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli)
- `az login` (and `az account set --subscription <id>` if you have several)
- `az extension add --name containerapp`
- Secrets in your shell or in `<repo>/.env`: `NESSIE_API_KEY`, `CRS_USERNAME`, `CRS_PASSWORD` (required), `ANTHROPIC_API_KEY` (optional; without it the agent runs rules-only). `NESSIE_BASE_URL`, `CRS_BASE_URL`, `LLM_MODEL` and `RISK_WINDOW_HOURS` are passed through if set.

## Run

From the repo root:

```bash
./infra/azure/deploy.sh
```

Optional overrides: `RESOURCE_GROUP` (default `lookout-rg`), `LOCATION` (`westus2`), `APP_NAME` (`lookout`), `ENV_NAME` (`lookout-env`), `ACR_NAME` (default derived from your subscription id), `IMAGE_TAG` (default: UTC timestamp). Example:

```bash
RESOURCE_GROUP=lookout-demo LOCATION=eastus ./infra/azure/deploy.sh
```

The script prints the URL at the end. The dashboard is at `https://<fqdn>/app/`, health at `/health`.

## What it creates

| Resource | Notes |
|---|---|
| Resource group | Holds everything; delete it to tear down. |
| Azure Container Registry (Basic) | Stores the image; built with `az acr build` from the repo root. Admin user is enabled so the app can pull. |
| Container Apps environment | Also creates a Log Analytics workspace for container logs. |
| Container app | External HTTPS ingress to port 8000, 0.5 vCPU / 1 GiB, **1 replica**. |

Secrets are stored as Container Apps secrets and exposed to the app via `secretref:` env vars. `.env` is never copied into the image (see `.dockerignore`), and the script never prints secret values.

## Single replica

All state (events, decisions, audit log) lives in memory (`backend/app/store.py`). The app is pinned to `min = max = 1` replica: more replicas would each hold different state, and scale-to-zero would lose it. A restart or new revision also clears state, so rerun the demo setup after a redeploy.

## Cost

ACR Basic (a few dollars a month) plus one small always-on container, and minimal Log Analytics ingestion. Tear it down after the event.

## Update

Rerun `./infra/azure/deploy.sh`. It reuses existing resources, builds a new image tag, refreshes secrets and rolls a new revision.

## Tear down

```bash
az group delete --name lookout-rg --yes --no-wait
```

## Path to production

| Today (hackathon) | Production |
|---|---|
| Capital One Nessie (mock bank data) | Plaid (or direct bank aggregator) for real transactions, with the parent's consent |
| CRS sandbox personas | Production bureau access through CRS, with permissible purpose and consent |
| In-memory store, single replica | Azure Database for PostgreSQL; then the app can scale out |
| Container Apps secrets | Azure Key Vault, read via managed identity |
| ACR admin credentials | Managed identity with `AcrPull` |
| Claude via the Anthropic API | Same, or Claude on Microsoft Foundry to keep inference inside Azure |
| Container logs in Log Analytics | Azure Monitor / Application Insights for logs, metrics and the decision audit trail |
