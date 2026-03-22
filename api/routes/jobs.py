"""Job API routes."""

from __future__ import annotations

import hashlib

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from api.dependencies import get_job_queue, get_job_store
from api.schemas import JobCreateRequest, JobCreateResponse
from models.job import Job

router = APIRouter()


def _query_hash(query: str) -> str:
    normalized = query.strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


@router.post("/jobs")
async def create_job(request: JobCreateRequest) -> JSONResponse:
    store = get_job_store()
    queue = get_job_queue()

    # Check cache: if a completed job exists with same query hash, return it
    qhash = _query_hash(request.query)
    try:
        recent = store.list_recent(limit=100)
        for existing in recent:
            if (
                existing.status.value == "completed"
                and existing.result
                and _query_hash(existing.query) == qhash
            ):
                return JSONResponse(
                    status_code=200,
                    content=existing.to_dict(),
                )
    except Exception:  # noqa: BLE001
        pass  # Cache check failure is non-fatal

    job = Job(query=request.query, webhook_url=request.webhook_url)
    store.create(job)
    queue.send({
        "job_id": job.job_id,
        "query": job.query,
        "webhook_url": job.webhook_url,
        "kickoff_first_half": request.kickoff_first_half,
        "kickoff_second_half": request.kickoff_second_half,
    })

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
async def list_jobs(limit: int = 20) -> dict:
    store = get_job_store()
    jobs = store.list_recent(limit=limit)
    return {"jobs": [j.to_dict() for j in jobs]}
