# Azure RBAC: add a teammate to the resource group

This is a copy/paste guide for granting a teammate access to this project’s Azure resource group.

## Prerequisites

```bash
brew install azure-cli   # macOS
az login                 # authenticate with Reichman University account
```

If your Azure org has multiple tenants/subscriptions, confirm you’re in the right tenant/subscription first:

```bash
az account show --output table
```

## Step 1 — Find the resource group name

```bash
az group list --output table
```

## Step 2 — Add teammate role assignment (resource-group scope)

Pick **one** of the roles below.

- **Owner**: full control (includes RBAC management). Use sparingly.
- **Contributor**: can create/update resources but **cannot** grant access to others. Often enough.

### Option A — Owner

```bash
SUB_ID=$(az account show --query id --output tsv)

az role assignment create \
  --role "Owner" \
  --assignee <teammate-email@post.runi.ac.il> \
  --scope "/subscriptions/$SUB_ID/resourceGroups/<RESOURCE_GROUP_NAME>"
```

### Option B — Contributor (recommended default)

```bash
SUB_ID=$(az account show --query id --output tsv)

az role assignment create \
  --role "Contributor" \
  --assignee <teammate-email@post.runi.ac.il> \
  --scope "/subscriptions/$SUB_ID/resourceGroups/<RESOURCE_GROUP_NAME>"
```

## Verify it worked

```bash
az role assignment list --resource-group <RESOURCE_GROUP_NAME> --output table
```

## Common gotchas / fixes

- **Must use `--scope` (not `--resource-group`) when creating assignments**: `az role assignment create` requires a full scope string.
- **Email must match the Azure AD account exactly**: use the teammate’s `@post.runi.ac.il` identity (or whichever identity appears in your Azure AD).
- **Both accounts must be in the same Azure AD tenant**: Reichman University. If you’re logged into the wrong tenant, switch:

```bash
az account tenant list -o table
az login --tenant <TENANT_ID>
```

- **If `--assignee` fails (“cannot find user”)**: try using the teammate’s object id instead of email:

```bash
az ad user show --id <teammate-email@post.runi.ac.il> --query id -o tsv
```

