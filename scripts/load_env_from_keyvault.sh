#!/usr/bin/env bash
set -euo pipefail

# Load secrets from Azure Key Vault into your current shell env.
#
# Usage:
#   export KV_NAME="football-hl-kv-39f206"
#   eval "$(./scripts/load_env_from_keyvault.sh)"
#
# Notes:
# - This prints `export ...` lines to stdout. It does NOT write any files.
# - Requires: Azure CLI login (`az login`) and permission to read Key Vault secrets.

KV_NAME="${KV_NAME:-}"
if [[ -z "$KV_NAME" ]]; then
  echo "KV_NAME is required (Key Vault name)." >&2
  exit 1
fi

get_secret() {
  local secret_name="$1"
  az keyvault secret show \
    --vault-name "$KV_NAME" \
    --name "$secret_name" \
    --query value -o tsv 2>/dev/null || true
}

ASSEMBLYAI_API_KEY="$(get_secret "assemblyai-api-key")"
API_FOOTBALL_KEY="$(get_secret "api-football-key")"
OPENAI_API_KEY="$(get_secret "openai-api-key")"
API_KEYS="$(get_secret "api-keys")"

if [[ -z "$ASSEMBLYAI_API_KEY" || -z "$API_FOOTBALL_KEY" ]]; then
  echo "Failed to read required secrets from Key Vault '$KV_NAME'." >&2
  echo "Need: assemblyai-api-key, api-football-key" >&2
  exit 1
fi

# Print shell-safe exports (no extra output).
cat <<EOF
export ASSEMBLYAI_API_KEY=$(printf '%q' "$ASSEMBLYAI_API_KEY")
export API_FOOTBALL_KEY=$(printf '%q' "$API_FOOTBALL_KEY")
export OPENAI_API_KEY=$(printf '%q' "$OPENAI_API_KEY")
export API_KEYS=$(printf '%q' "$API_KEYS")
EOF

