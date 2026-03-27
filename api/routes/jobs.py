"""Job API routes."""

from __future__ import annotations

import hashlib
from contextlib import suppress

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from api.dependencies import get_job_queue, get_job_store
from api.schemas import JobCreateRequest, JobCreateResponse
from catalog.loader import get_match
from models.job import Job

router = APIRouter()


def _job_cache_key(match_id: str, highlights_query: str) -> str:
    normalized = f"{match_id.strip().lower()}::{highlights_query.strip().lower()}"
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


@router.post("/jobs")
async def create_job(request: JobCreateRequest) -> JSONResponse:
    job_store = get_job_store()
    queue = get_job_queue()

    entry = get_match(request.match_id)
    if entry is None:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "unknown_match",
                    "message": f"Unknown match_id: {request.match_id!r}. Use GET /api/v1/matches.",
                }
            },
        )

    qhash = _job_cache_key(request.match_id, request.highlights_query)
    with suppress(Exception):
        recent = job_store.list_recent(limit=100)
        for existing in recent:
            if (
                existing.status.value == "completed"
                and existing.result
                and _job_cache_key(existing.match_id, existing.highlights_query) == qhash
            ):
                return JSONResponse(
                    status_code=200,
                    content=JobCreateResponse(
                        job_id=existing.job_id,
                        status=existing.status.value,
                        poll_url=f"/api/v1/jobs/{existing.job_id}",
                    ).model_dump(),
                )

    label = f"{entry.title} — {request.highlights_query}"
    job = Job(
        match_id=request.match_id.strip(),
        highlights_query=request.highlights_query,
        query=label,
        webhook_url=str(request.webhook_url) if request.webhook_url else None,
    )
    job_store.create(job)
    queue.send(
        {
            "job_id": job.job_id,
            "match_id": job.match_id,
            "highlights_query": job.highlights_query,
            "webhook_url": job.webhook_url,
        }
    )

    return JSONResponse(
        status_code=202,
        content=JobCreateResponse(
            job_id=job.job_id,
            status=job.status.value,
            poll_url=f"/api/v1/jobs/{job.job_id}",
        ).model_dump(),
    )


@router.get("/jobs/{job_id}", response_model=None)
async def get_job(job_id: str) -> JSONResponse | dict:
    store = get_job_store()
    job = store.get(job_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "not_found", "message": "Job not found"}},
        )
    return job.to_dict()


@router.get("/jobs")
async def list_jobs(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    store = get_job_store()
    jobs = store.list_recent(limit=limit)
    return {"jobs": [j.to_dict() for j in jobs]}
