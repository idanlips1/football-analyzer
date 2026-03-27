# Azure Deployment Guide

## Prerequisites

- Azure CLI (`az`) installed and logged in: `az login`
- Bicep installed: `az bicep install`
- Docker installed
- An Azure subscription

## 1. Configure environment variables

Set deployment-related variables (no API keys — those live in Key Vault; see below).

```bash
export RG_NAME="football-hl-rg"
export LOCATION="germanywestcentral"   # must match your subscription policy
export BASE_NAME="football-hl"
export KV_NAME="football-hl-kv-39f206"  # globally unique Key Vault name

# Optional
export DEPLOYMENT_NAME="main"
export IMAGE_TAG="latest"
```

**Secrets:** Populate Key Vault (`assemblyai-api-key`, `api-football-key`, `api-keys`, etc.) yourself (Azure Portal, `az keyvault secret set`, or `scripts/load_env_from_keyvault.sh` for local dev). The deploy script does not write them.

### Curated matches (no YouTube in the worker)

Videos are **uploaded ahead of time** to blob storage (`videos/<match_id>/match.mp4` + `metadata.json`).

Operator path: **`scripts/ingest_youtube_query.py`** — **search YouTube from free text**, confirm by **title + duration**, add/update **`catalog/data/matches.json`**, download, run full ingestion, and upload to Blob (see README).

`GET /api/v1/matches` lists matches that have **`videos/<match_id>/match.mp4`** and **`metadata.json`**. Jobs require **`pipeline/<match_id>/game.json`** and **`aligned_events.json`** (fully ingested).

## 2. Run one-shot deployment script

```bash
chmod +x scripts/deploy_azure_env.sh
./scripts/deploy_azure_env.sh
```

The script handles:

- provider registration
- resource group + key vault creation (if missing)
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
