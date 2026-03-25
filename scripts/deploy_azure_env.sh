#!/usr/bin/env bash
set -euo pipefail

# Azure deploy helper using environment variables only.
#
# Required env vars:
#   RG_NAME
#   LOCATION
#   BASE_NAME
#   KV_NAME
#   ASSEMBLYAI_API_KEY
#   API_FOOTBALL_KEY
#
# Optional env vars:
#   API_KEYS                 (defaults to "," => effectively no API auth)
#   OPENAI_API_KEY           (defaults to empty string)
#   DEPLOYMENT_NAME          (defaults to "main")
#   IMAGE_TAG                (defaults to "latest")
#
# Example:
#   export RG_NAME="football-hl-rg"
#   export LOCATION="israelcentral"
#   export BASE_NAME="football-hl"
#   export KV_NAME="football-hl-kv-39f206"
#   export ASSEMBLYAI_API_KEY="..."
#   export API_FOOTBALL_KEY="..."
#   export OPENAI_API_KEY="..."
#   export API_KEYS=","
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
require_env "ASSEMBLYAI_API_KEY"
require_env "API_FOOTBALL_KEY"

DEPLOYMENT_NAME="${DEPLOYMENT_NAME:-main}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
API_KEYS="${API_KEYS:-,}"
OPENAI_API_KEY="${OPENAI_API_KEY:-}"
ACR_NAME="${BASE_NAME//-/}acr"
CONTAINER_IMAGE="${ACR_NAME}.azurecr.io/football-analyzer:${IMAGE_TAG}"

echo "==> Ensuring required Azure providers are registered"
az provider register --namespace Microsoft.KeyVault --wait >/dev/null
az provider register --namespace Microsoft.Storage --wait >/dev/null
az provider register --namespace Microsoft.App --wait >/dev/null
az provider register --namespace Microsoft.ContainerRegistry --wait >/dev/null
az provider register --namespace Microsoft.OperationalInsights --wait >/dev/null

echo "==> Ensuring resource group exists"
if az group exists --name "$RG_NAME" | grep -q "true"; then
  RG_LOCATION="$(az group show --name "$RG_NAME" --query location -o tsv)"
  echo "Resource group '$RG_NAME' already exists in '$RG_LOCATION' (keeping as-is)."
else
  az group create --name "$RG_NAME" --location "$LOCATION" >/dev/null
fi

echo "==> Ensuring Key Vault exists"
if ! az keyvault show --name "$KV_NAME" --resource-group "$RG_NAME" >/dev/null 2>&1; then
  az keyvault create \
    --name "$KV_NAME" \
    --resource-group "$RG_NAME" \
    --location "$LOCATION" \
    --enable-rbac-authorization true \
    >/dev/null
fi

KV_ID="$(az keyvault show --name "$KV_NAME" --resource-group "$RG_NAME" --query id -o tsv)"
USER_ID="$(az ad signed-in-user show --query id -o tsv)"

echo "==> Ensuring caller can set Key Vault secrets"
if ! az role assignment list \
  --scope "$KV_ID" \
  --assignee-object-id "$USER_ID" \
  --query "[?roleDefinitionName=='Key Vault Secrets Officer'] | length(@)" \
  -o tsv | grep -q "^[1-9]"; then
  az role assignment create \
    --assignee-object-id "$USER_ID" \
    --assignee-principal-type User \
    --role "Key Vault Secrets Officer" \
    --scope "$KV_ID" \
    >/dev/null
  echo "Waiting 30s for Key Vault RBAC propagation..."
  sleep 30
fi

echo "==> Writing secrets to Key Vault"
az keyvault secret set --vault-name "$KV_NAME" --name "api-keys" --value "$API_KEYS" >/dev/null
az keyvault secret set --vault-name "$KV_NAME" --name "assemblyai-api-key" --value "$ASSEMBLYAI_API_KEY" >/dev/null
az keyvault secret set --vault-name "$KV_NAME" --name "api-football-key" --value "$API_FOOTBALL_KEY" >/dev/null
az keyvault secret set --vault-name "$KV_NAME" --name "openai-api-key" --value "$OPENAI_API_KEY" >/dev/null

echo "==> Deploying infrastructure"
set +e
az deployment group create \
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

ACR_SERVER="$(az deployment group show \
  --resource-group "$RG_NAME" \
  --name "$DEPLOYMENT_NAME" \
  --query "properties.outputs.acrLoginServer.value" -o tsv 2>/dev/null || true)"

if [[ -z "$ACR_SERVER" ]]; then
  ACR_SERVER="$(az acr show --name "$ACR_NAME" --resource-group "$RG_NAME" --query "loginServer" -o tsv)"
fi

echo "==> Building and pushing image: ${ACR_SERVER}/football-analyzer:${IMAGE_TAG}"
az acr login --name "$ACR_NAME" >/dev/null
if ! docker buildx version >/dev/null 2>&1; then
  echo "docker buildx is required to publish linux/amd64 images from Apple Silicon." >&2
  exit 1
fi
docker buildx build \
  --platform linux/amd64 \
  --tag "${ACR_SERVER}/football-analyzer:${IMAGE_TAG}" \
  --push \
  .

echo "==> Re-applying infrastructure with pushed image"
az deployment group create \
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
API_REVISION="$(az containerapp show \
  --name "${BASE_NAME}-api" \
  --resource-group "$RG_NAME" \
  --query "properties.latestRevisionName" -o tsv)"
WORKER_REVISION="$(az containerapp show \
  --name "${BASE_NAME}-worker" \
  --resource-group "$RG_NAME" \
  --query "properties.latestRevisionName" -o tsv)"

az containerapp revision restart \
  --name "${BASE_NAME}-api" \
  --resource-group "$RG_NAME" \
  --revision "$API_REVISION" \
  >/dev/null
az containerapp revision restart \
  --name "${BASE_NAME}-worker" \
  --resource-group "$RG_NAME" \
  --revision "$WORKER_REVISION" \
  >/dev/null

API_URL="$(az deployment group show \
  --resource-group "$RG_NAME" \
  --name "$DEPLOYMENT_NAME" \
  --query "properties.outputs.apiUrl.value" -o tsv)"

echo
echo "Deployment complete."
echo "API URL: ${API_URL}"
echo "Health:  curl ${API_URL}/api/v1/health"
