# Football Highlights Generator

Async REST API that generates highlights clips from famous football matches. Submit a natural-language query (e.g. "all goals and cards"), get back a download link when it's ready.

Powered by **API-Football** event data + **AssemblyAI** transcription for kickoff detection + **FFmpeg** clip cutting, deployed on **Azure Container Apps**.

## Architecture

```
POST /jobs → Storage Queue → Worker → pipeline (events → transcription → alignment → clips)
                                           ↓
                                    Blob Storage (highlights.mp4)
```

Full design: [`docs/2026-03-22-azure-async-deployment-design.md`](docs/2026-03-22-azure-async-deployment-design.md)

## Quickstart (local dev)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pre-commit install
```

Create `.env`:

| Variable | Purpose |
|----------|---------|
| `ASSEMBLYAI_API_KEY` | Transcription (AssemblyAI) |
| `API_FOOTBALL_KEY` | Match events (`v3.football.api-sports.io`) |
| `API_KEYS` | Comma-separated API keys for the service (e.g. `dev-key`) |

Run API and worker in separate terminals:

```bash
# Terminal 1 — API
uvicorn api.app:create_app --factory --reload --port 8000

# Terminal 2 — Worker
python -m worker
```

Check it's up:

```bash
curl http://localhost:8000/api/v1/health
```

## API Reference

All routes are prefixed `/api/v1/`. Authentication via `X-API-Key` header.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/matches` | List curated catalog |
| `POST` | `/jobs` | Submit a highlights job → `202` with `job_id` |
| `GET` | `/jobs/{job_id}` | Poll job status and result |
| `GET` | `/jobs?limit=20` | List recent jobs |

### Submit a job

```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "X-API-Key: dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "match_id": "istanbul-2005",
    "highlights_query": "all goals and penalties",
    "webhook_url": "https://example.com/webhook"   # optional
  }'
```

Response `202`:
```json
{ "job_id": "abc123", "status": "queued", "poll_url": "/api/v1/jobs/abc123" }
```

### Poll for result

```bash
curl http://localhost:8000/api/v1/jobs/abc123 -H "X-API-Key: dev-key"
```

Response when complete:
```json
{
  "job_id": "abc123",
  "status": "completed",
  "result": {
    "download_url": "https://…blob.core.windows.net/highlights/…?sv=…",
    "duration_seconds": 142.5,
    "clip_count": 7,
    "expires_at": "2026-03-28T12:00:00Z"
  }
}
```

## Curated Catalog

Videos are pre-uploaded to Azure Blob Storage — no YouTube downloads at request time. To add a new match:

```bash
python scripts/upload_catalog_match.py
```

Then add its entry to `catalog/data/matches.json`.

## Deployment

See [DEPLOY.md](DEPLOY.md) for full Azure deployment instructions (Bicep, ACR, Key Vault).

## Testing

```bash
pytest
```

## Code Style

Type annotations required on all functions. Linting runs on commit via pre-commit (ruff, mypy, bandit).

```bash
ruff check .
mypy .
bandit -r . -c pyproject.toml
```
