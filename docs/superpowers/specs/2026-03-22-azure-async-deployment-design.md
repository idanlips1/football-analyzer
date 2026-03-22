# Azure Async Deployment Design

**Date:** 2026-03-22
**Status:** Draft
**Goal:** Deploy football-analyzer as a production-ready async REST API on Azure, consumption-based.

---

## Context

Football-analyzer is a 5-stage pipeline that generates highlight clips from full match videos. Currently runs as an interactive CLI. We're deploying it as an async REST API on Azure for a small product (tens of users, few jobs/day), optimizing for minimal cost.

## Architecture

```
Client --> FastAPI (ACA) --> Azure Storage Queue --> Worker (ACA, KEDA-scaled)
                |                                        |
          Table Storage <------ status updates ----------+
                                                         |
                                                   Blob Storage
                                                         |
                                                   Webhook POST (optional)
```

### Components

| Component | Azure Service | Purpose |
|-----------|--------------|---------|
| API | Azure Container Apps (scale-to-zero) | REST endpoints for job submission + polling |
| Worker | Azure Container Apps (KEDA queue scaler) | Long-lived queue consumer, runs pipeline |
| Queue | Azure Storage Queue | Decouples API from worker, built-in retry + dead-letter |
| Blob | Azure Blob Storage | Videos, pipeline artifacts, highlights output |
| State | Azure Table Storage | Job records (status, progress, result URL, webhook URL) |

All storage services live in a single Azure Storage Account.

### Worker Scaling

- KEDA scales on queue depth: 0 replicas when empty, 1+ when messages arrive
- Sequential processing within each replica
- Scale-to-zero after ~5 min idle
- Max 3 replicas (configurable) if queue backs up
- No container-per-request — warm worker stays alive between jobs

## API Design

**Base:** `https://<app>.azurecontainerapps.io/api/v1`

### POST /jobs

Submit a highlights generation job.

```json
// Request
{
  "query": "Liverpool vs Man City 2025 Champions League",
  "webhook_url": "https://example.com/callback"  // optional
}

// Response: 202 Accepted
{
  "job_id": "abc-123",
  "status": "queued",
  "poll_url": "/api/v1/jobs/abc-123"
}
```

### GET /jobs/{job_id}

Poll job status.

```json
// Response: 200
{
  "job_id": "abc-123",
  "status": "completed",       // queued | processing | completed | failed
  "progress": "clip_building", // current stage (null when queued/done)
  "query": "Liverpool vs Man City 2025 Champions League",
  "result": {
    "download_url": "https://...blob.core.windows.net/highlights/abc-123.mp4?sas=...",
    "duration_seconds": 342,
    "clip_count": 8,
    "expires_at": "2026-03-23T10:00:00Z"
  },
  "error": null,
  "created_at": "2026-03-22T10:00:00Z"
}
```

### GET /jobs?limit=20

List recent jobs.

### Webhook Payload

POST to `webhook_url` on completion or failure:

```json
{
  "job_id": "abc-123",
  "status": "completed",
  "result": { "download_url": "...", "duration_seconds": 342 }
}
```

### Key Decisions

- `202 Accepted` — job is queued, not yet complete
- SAS URLs expire after 24h
- No auth initially (API key middleware added later)
- Progress field maps to pipeline stage names

## Storage Layout

### Blob Containers

```
videos/                          # Downloaded source videos
  {video_id}/
    metadata.json
    video.mp4
    audio.wav

pipeline/                        # Intermediate pipeline artifacts
  {video_id}/
    match_events.json
    transcription.json
    aligned_events.json

highlights/                      # Final outputs (SAS-accessible)
  {video_id}/
    {query_hash}.mp4
```

**Why 3 containers:**
- `videos/` — large files, lifecycle policy to delete after 30 days
- `pipeline/` — small JSON, kept indefinitely for fast re-runs
- `highlights/` — client-facing, SAS scoped to this container only

### Queue

- `job-queue` — new job messages
- `job-queue-poison` — dead-letter after 5 failed attempts

### Table

- `jobs` — partition key: `YYYY-MM-DD`, row key: `job_id`

## Caching

### Pipeline workspace cache (per video)

If a video was already downloaded + transcribed for a previous job, new jobs reuse those artifacts from Blob Storage. Keyed by `video_id`.

### Highlights result cache (per query)

Exact same query for same match returns existing highlights. Keyed by `video_id + query_hash`.

### API behavior

`POST /jobs` checks for cached result first. If found, returns immediately with `status: completed`. If video is cached but query is new, worker skips stages 1-4 and only runs clip building.

## Worker Design

### Job processing flow

1. Dequeue message -> `{ job_id, query, webhook_url }`
2. Update job status -> `processing`
3. Run pipeline with `BlobStorage` backend (progress updated at each stage)
4. On success: upload highlights, generate SAS URL, update job -> `completed`, fire webhook
5. On failure: update job -> `failed` with error, fire webhook with error

### Retry strategy

- Queue visibility timeout: 35 min (longer than max job time)
- Max dequeue count: 3 -> poison queue after 3 failures
- Retries are idempotent (completed stages cached in blob)

### Container specs

- 2 vCPU, 4 GB RAM per replica
- 30 min job timeout
- Concurrency: 1 job per replica

## Pipeline Integration

### What changes

| Component | Change |
|-----------|--------|
| `utils/storage.py` | Add `BlobStorage` implementing `StorageBackend` |
| `utils/webhook.py` | New — webhook delivery with retry |
| `config/settings.py` | Add Azure config vars (connection string, container names, SAS expiry) |
| `api/` | New — FastAPI app, routes, schemas, dependencies |
| `worker/` | New — queue consumer + pipeline runner |
| `infra/bicep/` | New — Infrastructure as Code |
| `Dockerfile` | Modified — serves API via uvicorn |
| `requirements.txt` | Add `azure-storage-blob`, `azure-storage-queue`, `azure-data-tables`, `httpx` |

### What stays unchanged

- `pipeline/*.py` — all 5 stages untouched
- `models/*.py` — no changes
- `utils/ffmpeg.py` — works on local temp files as before

### Backend switching

Env var `STORAGE_BACKEND=local|azure` controls which implementations are injected. Local dev uses `LocalStorage` + in-memory queue/job store. Deployed uses Azure services.

## Project Structure

```
football-analyzer/
├── api/                          # NEW
│   ├── __init__.py
│   ├── app.py                    # FastAPI factory, middleware, lifespan
│   ├── routes/
│   │   ├── __init__.py
│   │   └── jobs.py               # POST/GET /jobs
│   ├── schemas.py                # Pydantic request/response models
│   └── dependencies.py           # Shared deps injection
│
├── worker/                       # NEW
│   ├── __init__.py
│   └── runner.py                 # Queue poll loop, pipeline execution, status updates
│
├── infra/                        # NEW
│   ├── bicep/
│   │   ├── main.bicep
│   │   └── parameters.json
│   └── Dockerfile.worker         # If worker needs separate image
│
├── utils/
│   ├── storage.py                # MODIFIED — add BlobStorage
│   ├── webhook.py                # NEW
│   └── ...existing...
│
├── config/settings.py            # MODIFIED
├── pipeline/                     # UNCHANGED
├── models/                       # UNCHANGED
├── tests/                        # EXTENDED
├── Dockerfile                    # MODIFIED
└── requirements.txt              # MODIFIED
```

## New Dependencies

- `azure-storage-blob` — blob upload/download/SAS
- `azure-storage-queue` — queue send/receive
- `azure-data-tables` — Table Storage CRUD
- `httpx` — async HTTP for webhook delivery

## Local Development

```bash
# Start API
uvicorn api.app:app --reload --port 8000

# Start worker (separate terminal)
python -m worker.runner
```

Locally uses `LocalStorage`, in-memory queue, in-memory job store. No Azure emulator needed. Real API keys (AssemblyAI, API-Football) required for full pipeline runs.

## Testing

| Layer | How |
|-------|-----|
| API routes | `pytest` + FastAPI `TestClient`, mock queue/table |
| Worker | `pytest`, mock pipeline + table + queue |
| BlobStorage | `pytest`, mock `azure.storage.blob` client |
| Webhook | `pytest` + `httpx` mock |
| Pipeline | Existing tests unchanged |
| Integration | Mock external APIs, mock Azure clients |

All Azure clients mocked via `unittest.mock`. No emulators.

## Infrastructure (Azure Bicep)

Single `main.bicep` provisions:
- Resource Group
- Storage Account (blob, queue, table)
- Container Apps Environment
- API Container App (scale-to-zero, port 8000)
- Worker Container App (KEDA queue scaler, scale 0-3)
- Container Registry (ACR) for Docker images

## Cost Estimate (low volume)

| Service | Cost |
|---------|------|
| ACA (API, scale-to-zero) | ~$0 when idle, ~$0.01/hour when active |
| ACA (Worker, scale-to-zero) | ~$0 when idle, ~$0.04/hour per job (2 vCPU) |
| Blob Storage | ~$0.02/GB/month |
| Table + Queue | Pennies/month |
| **Total at ~5 jobs/day** | **~$5-10/month** |
