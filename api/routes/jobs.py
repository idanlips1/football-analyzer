"""Job API routes."""

from __future__ import annotations

from fastapi import APIRouter

from api.dependencies import get_job_store

router = APIRouter()


@router.get("/jobs")
async def list_jobs(limit: int = 20) -> dict:
    store = get_job_store()
    jobs = store.list_recent(limit=limit)
    return {"jobs": [j.to_dict() for j in jobs]}
