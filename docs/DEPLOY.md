# Azure Deployment Guide

## Prerequisites

- Azure CLI (`az`) installed and logged in: `az login`
- Bicep installed: `az bicep install`
- Docker installed
- An Azure subscription

## 1. Configure environment variables

Use environment variables for secrets and run the deployment script.

```bash
export RG_NAME="football-hl-rg"
export LOCATION="israelcentral"   # must match your subscription policy
export BASE_NAME="football-hl"
export KV_NAME="football-hl-kv-39f206"  # globally unique Key Vault name

# Required API keys
export ASSEMBLYAI_API_KEY="<your-assemblyai-key>"
export API_FOOTBALL_KEY="<your-api-football-key>"

# Optional
export OPENAI_API_KEY="<your-openai-key>"   # default empty if unset
export API_KEYS=","                         # "," disables API auth for now
export DEPLOYMENT_NAME="main"
export IMAGE_TAG="latest"
```

### Curated matches (no YouTube in the worker)

Videos are **uploaded ahead of time** to blob storage (`videos/<match_id>/match.mp4` + `metadata.json`).

Operator options:

- `scripts/upload_catalog_match.py`: upload a local `.mp4` (or a specific YouTube URL)
- `scripts/ingest_youtube_query.py`: end-to-end helper — **search YouTube from free text**, confirm by **title + duration**, add/update the catalog entry, download, and run ingestion

The API only accepts jobs for known catalog ids — list them with `GET /api/v1/matches`.

## 2. Run one-shot deployment script

```bash
chmod +x scripts/deploy_azure_env.sh
./scripts/deploy_azure_env.sh
```

The script handles:
- provider registration
- resource group + key vault creation (if missing)
- granting caller `Key Vault Secrets Officer` (if missing)
- writing secrets to Key Vault
- Bicep deployment
- image build/push
- API + worker restart

## 6. Verify

```bash
# Get API URL
API_URL=$(az deployment group show \
  --resource-group "$RG_NAME" \
  --name "$DEPLOYMENT_NAME" \
  --query 'properties.outputs.apiUrl.value' -o tsv)

# Health check
curl $API_URL/api/v1/health

# List catalog matches
curl -s $API_URL/api/v1/matches \
  -H "X-API-Key: <value from api-keys secret>"

# Submit a job (match_id from the catalog)
curl -X POST $API_URL/api/v1/jobs \
  -H "X-API-Key: <value from api-keys secret>" \
  -H "Content-Type: application/json" \
  -d '{"match_id": "istanbul-2005", "highlights_query": "goals and cards"}'
```

## Updating

After code changes:

```bash
./scripts/deploy_azure_env.sh
```
