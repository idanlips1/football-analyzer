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

### Scaling

Always-on, minimal resources. No KEDA, no scale-to-zero — unnecessary complexity for this volume.

- **API:** 1 replica, 0.25 vCPU, 0.5 GB RAM (FastAPI serving lightweight JSON)
- **Worker:** 1 replica, 2 vCPU, 4 GB RAM (FFmpeg needs CPU/memory)

One worker processing jobs sequentially is sufficient for a few jobs/day. If volume grows, bump to 2 replicas later.

### Why a queue

The queue decouples the API from the worker so the API can return `202` instantly instead of blocking for 10-30 minutes. Benefits:

- **Reliability:** if the worker crashes mid-job, the message reappears and gets retried automatically
- **Backpressure:** if multiple jobs arrive while the worker is busy, they queue up and process in order
- **Simplicity:** no background threads, no in-process job management, no lost jobs on restart

One queue is enough — we have one type of work (generate highlights). Multiple queues would only matter with different job types or priority tiers.

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

### GET /health

Liveness/readiness probe for ACA.

```json
// Response: 200
{ "status": "ok" }
```

### Key Decisions

- `202 Accepted` — job is queued, not yet complete
- Cache hit on `POST /jobs`: returns `200` with `status: completed` and existing `download_url` (no queue message)
- Cache miss: returns `202 Accepted` with `status: queued`
- SAS URLs expire after 24h
- No auth initially (API key middleware added later)
- Progress field maps to pipeline stage names
- Error response: `{ "error": { "code": "not_found", "message": "Job not found" } }`

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

`POST /jobs` checks for cached result first. If found, returns `200` with `status: completed` and the existing SAS URL (no queue message). If video is cached but query is new, worker skips stages 1-4 and only runs clip building.

Cache key for highlights: SHA-256 of `video_id + normalized_query` (lowercased, stripped). Two differently-worded queries that produce different event filters are treated as separate cache entries — this is acceptable; over-caching is worse than occasional re-cuts.

### Duplicate job race condition

Two near-simultaneous POSTs for the same query could both miss cache and both queue jobs. At this scale (few jobs/day) this is acceptable — the second job will be a no-op since all stages are cached by the time it runs. No distributed locking needed.

## Worker Design

### Job processing flow

1. Dequeue message -> `{ job_id, query, webhook_url }`
2. Update job status -> `processing`
3. Run pipeline with `BlobStorage` backend (progress updated at each stage)
4. On success: upload highlights, generate SAS URL, update job -> `completed`, fire webhook
5. On failure: update job -> `failed` with error, fire webhook with error

### Local temp staging (BlobStorage)

FFmpeg requires local files. `BlobStorage` handles this transparently:

1. `local_path(video_id, filename)` → downloads blob to a temp directory if not already present, returns local `Path`
2. Pipeline stages run against local files as before (no code changes)
3. `write_json()` / file writes → upload to blob after local write
4. On job completion (success or failure) → delete the temp directory

Temp dir: `/tmp/football-analyzer/{job_id}/` — isolated per job, cleaned up automatically.

### Worker ephemeral storage

90-min video (~2-4 GB) + audio (~200 MB) + clips (~1 GB) + highlights (~500 MB) ≈ up to 6 GB peak. Worker requests **20 GB ephemeral storage** per replica to handle worst case with headroom.

### Retry strategy

- Queue visibility timeout: 65 min (longer than max job time)
- Max dequeue count: 3 -> poison queue after 3 failures
- Retries are idempotent (completed stages cached in blob)

### Container specs

- 2 vCPU, 4 GB RAM per replica
- 45 min job timeout (covers slow downloads + long transcriptions)
- 20 GB ephemeral storage
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

- `pipeline/*.py` — all 5 stages untouched (they receive a `StorageBackend`; `BlobStorage` handles blob↔local staging transparently)
- `models/*.py` — no changes
- `utils/ffmpeg.py` — works on local temp files as before

**Note:** Legacy `pipeline/ingestion.py` hardcodes `PIPELINE_WORKSPACE` and is not used. The worker uses `match_finder.py` which accepts `StorageBackend`.

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

### Webhook retry

3 attempts with exponential backoff (1s, 4s, 16s). Failures logged but do not affect job status — the job is still `completed`/`failed` regardless of webhook delivery.

### Docker image strategy

Single image, two entrypoints:
- API: `CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]`
- Worker: `CMD ["python", "-m", "worker.runner"]`

ACA container apps override the command per service. No separate `Dockerfile.worker` needed.

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
- API Container App (always-on, 1 replica, 0.25 vCPU / 0.5 GB, port 8000)
- Worker Container App (always-on, 1 replica, 2 vCPU / 4 GB)
- Container Registry (ACR) for Docker images

## Cost Estimate (low volume)

| Service | Cost |
|---------|------|
| ACA (API, always-on, 0.25 vCPU) | ~$7/month |
| ACA (Worker, always-on, 2 vCPU) | ~$58/month |
| Blob Storage | ~$0.02/GB/month |
| Table + Queue | Pennies/month |
| **Total** | **~$65-70/month** |

Higher than scale-to-zero but simpler, no cold starts, and predictable billing.
