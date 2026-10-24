#!/usr/bin/env bash
# Deploy Lookout to Azure Container Apps.
#
#   ./infra/azure/deploy.sh            # create, or update in place if it already exists
#
# Builds the image in the cloud (az acr build), so Docker is not needed locally.
# Secrets come from the current environment, or from <repo>/.env if present.
# Secret values are never printed.
#
# State is in memory: the app MUST run as exactly one replica (min = max = 1).
set -euo pipefail

# ---------- Settings (override any of these from the environment) ----------
RESOURCE_GROUP="${RESOURCE_GROUP:-lookout-rg}"
LOCATION="${LOCATION:-westus2}"
APP_NAME="${APP_NAME:-lookout}"
ENV_NAME="${ENV_NAME:-lookout-env}"
# ACR names are global, 5-50 lowercase alphanumerics. Default is derived from the
# subscription id so reruns reuse the same registry.
ACR_NAME="${ACR_NAME:-}"
IMAGE_REPO="${IMAGE_REPO:-lookout}"
IMAGE_TAG="${IMAGE_TAG:-$(date -u +%Y%m%d%H%M%S)}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# ---------- Load .env without executing it ----------
# Values such as passwords can contain & or #, so the file is parsed, not sourced.
# Variables already set in the environment win over .env.
KNOWN_KEYS="NESSIE_API_KEY NESSIE_BASE_URL CRS_USERNAME CRS_PASSWORD CRS_BASE_URL ANTHROPIC_API_KEY LLM_MODEL RISK_WINDOW_HOURS"

load_env_file() {
  local file="$1" line key val
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "${line//[[:space:]]/}" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"
    key="${key#export }"
    key="${key//[[:space:]]/}"
    val="${line#*=}"
    # Strip one pair of surrounding quotes, if any.
    if [[ "$val" =~ ^\"(.*)\"$ || "$val" =~ ^\'(.*)\'$ ]]; then
      val="${BASH_REMATCH[1]}"
    fi
    case " $KNOWN_KEYS " in
      *" $key "*)
        if [[ -z "${!key:-}" ]]; then
          export "$key=$val"
        fi
        ;;
    esac
  done < "$file"
}

if [[ -f "$ENV_FILE" ]]; then
  log "Reading settings from .env (values not shown)"
  load_env_file "$ENV_FILE"
fi

# ---------- Preflight ----------
command -v az >/dev/null 2>&1 || die "Azure CLI (az) not found. Install it first."
az account show >/dev/null 2>&1 || die "Not logged in. Run: az login"
az extension show --name containerapp >/dev/null 2>&1 \
  || die "Container Apps extension missing. Run: az extension add --name containerapp"

for required in NESSIE_API_KEY CRS_USERNAME CRS_PASSWORD; do
  [[ -n "${!required:-}" ]] || die "$required is not set (environment or .env)."
done
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "WARNING: ANTHROPIC_API_KEY is not set; the agent will run rules-only."
fi

if [[ -z "$ACR_NAME" ]]; then
  SUB_ID="$(az account show --query id -o tsv)"
  ACR_NAME="lookout$(printf '%s' "$SUB_ID" | tr -d '-' | cut -c1-10)"
fi
[[ "$ACR_NAME" =~ ^[a-z0-9]{5,50}$ ]] || die "ACR_NAME must be 5-50 lowercase letters/digits (got '$ACR_NAME')."

echo "Resource group : $RESOURCE_GROUP ($LOCATION)"
echo "Registry       : $ACR_NAME"
echo "Environment    : $ENV_NAME"
echo "App            : $APP_NAME"
echo "Image tag      : $IMAGE_REPO:$IMAGE_TAG"

# ---------- Resource group ----------
log "Resource group"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

# ---------- Container registry ----------
log "Container registry (Basic)"
if az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --output none 2>/dev/null; then
  echo "Registry exists, reusing."
else
  az acr create --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" --sku Basic --admin-enabled true --output none
fi
az acr update --name "$ACR_NAME" --admin-enabled true --output none
ACR_SERVER="$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)"
ACR_USER="$(az acr credential show --name "$ACR_NAME" --query username -o tsv)"
ACR_PASS="$(az acr credential show --name "$ACR_NAME" --query 'passwords[0].value' -o tsv)"
IMAGE="$ACR_SERVER/$IMAGE_REPO:$IMAGE_TAG"

# ---------- Build image in the cloud ----------
log "Building $IMAGE with ACR Tasks (respects .dockerignore; .env is excluded)"
az acr build --registry "$ACR_NAME" --image "$IMAGE_REPO:$IMAGE_TAG" \
  --file "$REPO_ROOT/Dockerfile" "$REPO_ROOT"

# ---------- Container Apps environment ----------
log "Container Apps environment"
if az containerapp env show --name "$ENV_NAME" --resource-group "$RESOURCE_GROUP" --output none 2>/dev/null; then
  echo "Environment exists, reusing."
else
  az containerapp env create --name "$ENV_NAME" --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" --output none
fi

# ---------- Secrets and env vars ----------
# Secret names: lowercase letters, digits and dashes. Values only go to az, never to stdout.
SECRETS=(
  "nessie-api-key=$NESSIE_API_KEY"
  "crs-username=$CRS_USERNAME"
  "crs-password=$CRS_PASSWORD"
)
ENV_VARS=(
  "NESSIE_API_KEY=secretref:nessie-api-key"
  "CRS_USERNAME=secretref:crs-username"
  "CRS_PASSWORD=secretref:crs-password"
)
if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
  SECRETS+=("anthropic-api-key=$ANTHROPIC_API_KEY")
  ENV_VARS+=("ANTHROPIC_API_KEY=secretref:anthropic-api-key")
fi
# Non-secret settings, passed only when set (otherwise config.py defaults apply).
for plain in NESSIE_BASE_URL CRS_BASE_URL LLM_MODEL RISK_WINDOW_HOURS; do
  if [[ -n "${!plain:-}" ]]; then
    ENV_VARS+=("$plain=${!plain}")
  fi
done

# ---------- Container app (create or update) ----------
log "Container app (single replica: state is in memory)"
if az containerapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --output none 2>/dev/null; then
  echo "App exists, updating secrets and image."
  az containerapp registry set --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
    --server "$ACR_SERVER" --username "$ACR_USER" --password "$ACR_PASS" --output none
  az containerapp secret set --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
    --secrets "${SECRETS[@]}" --output none
  az containerapp update --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
    --image "$IMAGE" \
    --min-replicas 1 --max-replicas 1 \
    --set-env-vars "${ENV_VARS[@]}" \
    --output none
else
  az containerapp create --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
    --environment "$ENV_NAME" \
    --image "$IMAGE" \
    --registry-server "$ACR_SERVER" --registry-username "$ACR_USER" --registry-password "$ACR_PASS" \
    --ingress external --target-port 8000 \
    --min-replicas 1 --max-replicas 1 \
    --cpu 0.5 --memory 1.0Gi \
    --secrets "${SECRETS[@]}" \
    --env-vars "${ENV_VARS[@]}" \
    --output none
fi

FQDN="$(az containerapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn -o tsv)"

log "Done"
echo "API       : https://$FQDN"
echo "Health    : https://$FQDN/health"
echo "Dashboard : https://$FQDN/app/"
