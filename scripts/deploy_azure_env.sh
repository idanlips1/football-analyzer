#!/usr/bin/env bash
set -euo pipefail

# Quieter Azure CLI / Bicep (suppress WARNING lines; real errors still fail the command)
export AZURE_CORE_ONLY_SHOW_ERRORS=true

# Azure deploy helper using environment variables only.
#
# Required env vars:
#   RG_NAME
#   LOCATION
#   BASE_NAME
#   KV_NAME
#
# Optional env vars:
#   DEPLOYMENT_NAME          (defaults to "main")
#   IMAGE_TAG                (defaults to "latest")
#
# Application secrets (AssemblyAI, API-Football, etc.) are not written by this
# script — manage them in Key Vault separately (portal, CLI, or your own process).
#
# Example:
#   export RG_NAME="football-hl-rg"
#   export LOCATION="israelcentral"
#   export BASE_NAME="football-hl"
#   export KV_NAME="football-hl-kv-39f206"
#   ./scripts/deploy_azure_env.sh

require_env() {
  local var_name="$1"
  if [[ -z "${!var_name:-}" ]]; then
    echo "Missing required environment variable: $var_name" >&2
    exit 1
  fi
}

require_env "RG_NAME"
require_env "LOCATION"
require_env "BASE_NAME"
require_env "KV_NAME"

DEPLOYMENT_NAME="${DEPLOYMENT_NAME:-main}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
ACR_NAME="${BASE_NAME//-/}acr"
CONTAINER_IMAGE="${ACR_NAME}.azurecr.io/football-analyzer:${IMAGE_TAG}"

echo "==> Ensuring required Azure providers are registered"
az provider register --only-show-errors --namespace Microsoft.KeyVault --wait >/dev/null
az provider register --only-show-errors --namespace Microsoft.Storage --wait >/dev/null
az provider register --only-show-errors --namespace Microsoft.App --wait >/dev/null
az provider register --only-show-errors --namespace Microsoft.ContainerRegistry --wait >/dev/null
az provider register --only-show-errors --namespace Microsoft.OperationalInsights --wait >/dev/null

echo "==> Ensuring resource group exists"
if az group exists --only-show-errors --name "$RG_NAME" | grep -q "true"; then
  RG_LOCATION="$(az group show --only-show-errors --name "$RG_NAME" --query location -o tsv)"
  echo "Resource group '$RG_NAME' already exists in '$RG_LOCATION' (keeping as-is)."
else
  az group create --only-show-errors --name "$RG_NAME" --location "$LOCATION" >/dev/null
fi

echo "==> Ensuring Key Vault exists"
if ! az keyvault show --only-show-errors --name "$KV_NAME" --resource-group "$RG_NAME" >/dev/null 2>&1; then
  az keyvault create \
    --only-show-errors \
    --name "$KV_NAME" \
    --resource-group "$RG_NAME" \
    --location "$LOCATION" \
    --enable-rbac-authorization true \
    >/dev/null
fi

echo "==> Deploying infrastructure"
set +e
az deployment group create \
  --only-show-errors \
  --name "$DEPLOYMENT_NAME" \
  --resource-group "$RG_NAME" \
  --template-file "infra/bicep/main.bicep" \
  --parameters \
    baseName="$BASE_NAME" \
    location="$LOCATION" \
    keyVaultName="$KV_NAME" \
    containerImage="$CONTAINER_IMAGE" \
  >/dev/null
FIRST_DEPLOY_EXIT=$?
set -e

if [[ $FIRST_DEPLOY_EXIT -ne 0 ]]; then
  echo "Initial deployment returned errors (often transient on first run). Continuing..."
fi

ACR_SERVER=$(az deployment group show \
  --only-show-errors \
  --resource-group "$RG_NAME" \
  --name "$DEPLOYMENT_NAME" \
  --query 'properties.outputs.acrLoginServer.value' \
  -o tsv 2>/dev/null || true)

if [[ -z "$ACR_SERVER" ]]; then
  ACR_SERVER=$(az acr show \
    --only-show-errors \
    --name "$ACR_NAME" \
    --resource-group "$RG_NAME" \
    --query 'loginServer' \
    -o tsv)
fi

echo "==> Building and pushing image: ${ACR_SERVER}/football-analyzer:${IMAGE_TAG}"
az acr login --only-show-errors --name "$ACR_NAME" >/dev/null
if ! docker buildx version >/dev/null 2>&1; then
  echo "docker buildx is required to publish linux/amd64 images from Apple Silicon." >&2
  exit 1
fi
docker buildx build \
  --quiet \
  --platform linux/amd64 \
  --tag "${ACR_SERVER}/football-analyzer:${IMAGE_TAG}" \
  --push \
  .

echo "==> Re-applying infrastructure with pushed image"
az deployment group create \
  --only-show-errors \
  --name "$DEPLOYMENT_NAME" \
  --resource-group "$RG_NAME" \
  --template-file "infra/bicep/main.bicep" \
  --parameters \
    baseName="$BASE_NAME" \
    location="$LOCATION" \
    keyVaultName="$KV_NAME" \
    containerImage="$CONTAINER_IMAGE" \
  >/dev/null

echo "==> Restarting container apps"
API_REVISION=$(az containerapp show \
  --only-show-errors \
  --name "${BASE_NAME}-api" \
  --resource-group "$RG_NAME" \
  --query 'properties.latestRevisionName' \
  -o tsv)
WORKER_REVISION=$(az containerapp show \
  --only-show-errors \
  --name "${BASE_NAME}-worker" \
  --resource-group "$RG_NAME" \
  --query 'properties.latestRevisionName' \
  -o tsv)

az containerapp revision restart \
  --only-show-errors \
  --name "${BASE_NAME}-api" \
  --resource-group "$RG_NAME" \
  --revision "$API_REVISION" \
  >/dev/null
az containerapp revision restart \
  --only-show-errors \
  --name "${BASE_NAME}-worker" \
  --resource-group "$RG_NAME" \
  --revision "$WORKER_REVISION" \
  >/dev/null

API_URL=$(az deployment group show \
  --only-show-errors \
  --resource-group "$RG_NAME" \
  --name "$DEPLOYMENT_NAME" \
  --query 'properties.outputs.apiUrl.value' \
  -o tsv)

echo
echo "Deployment complete."
echo "API URL: ${API_URL}"
echo "Health:  curl ${API_URL}/api/v1/health"
