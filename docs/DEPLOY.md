# Azure Deployment Guide

## Prerequisites

- Azure CLI (`az`) installed and logged in: `az login`
- Docker installed
- An Azure subscription

## 1. Create Resource Group

```bash
az group create --name football-hl-rg --location eastus
```

## 2. Deploy Infrastructure (Bicep)

```bash
az deployment group create \
  --resource-group football-hl-rg \
  --template-file infra/bicep/main.bicep \
  --parameters \
    containerImage='footballhlacr.azurecr.io/football-analyzer:latest' \
    apiKeys='<your-api-key>' \
    assemblyaiApiKey='<your-assemblyai-key>' \
    apiFootballKey='<your-api-football-key>' \
    openaiApiKey='<your-openai-key>'
```

This provisions: Storage Account (blob/queue/table), ACR, ACA environment, API + Worker container apps, Log Analytics.

## 3. Build & Push Docker Image

```bash
# Get ACR login server from deployment output
ACR_SERVER=$(az deployment group show \
  --resource-group football-hl-rg \
  --name main \
  --query 'properties.outputs.acrLoginServer.value' -o tsv)

# Login to ACR
az acr login --name footballhlacr

# Build and push
docker build -t $ACR_SERVER/football-analyzer:latest .
docker push $ACR_SERVER/football-analyzer:latest
```

## 4. Restart Container Apps (pick up new image)

```bash
az containerapp revision restart \
  --name football-hl-api \
  --resource-group football-hl-rg

az containerapp revision restart \
  --name football-hl-worker \
  --resource-group football-hl-rg
```

## 5. Verify

```bash
# Get API URL
API_URL=$(az deployment group show \
  --resource-group football-hl-rg \
  --name main \
  --query 'properties.outputs.apiUrl.value' -o tsv)

# Health check
curl $API_URL/api/v1/health

# Submit a job
curl -X POST $API_URL/api/v1/jobs \
  -H "X-API-Key: <your-api-key>" \
  -H "Content-Type: application/json" \
  -d '{"query": "Liverpool vs Man City 2025"}'
```

## Updating

After code changes:

```bash
docker build -t $ACR_SERVER/football-analyzer:latest .
docker push $ACR_SERVER/football-analyzer:latest
az containerapp update --name football-hl-api --resource-group football-hl-rg --image $ACR_SERVER/football-analyzer:latest
az containerapp update --name football-hl-worker --resource-group football-hl-rg --image $ACR_SERVER/football-analyzer:latest
```
