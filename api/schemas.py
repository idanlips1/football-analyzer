"""Pydantic request/response models for the API."""

from __future__ import annotations

from pydantic import BaseModel


class JobCreateRequest(BaseModel):
    query: str
    webhook_url: str | None = None
    kickoff_first_half: float | None = None
    kickoff_second_half: float | None = None


class JobResultResponse(BaseModel):
    download_url: str
    duration_seconds: float
    clip_count: int
    expires_at: str


class JobResponse(BaseModel):
    job_id: str
    status: str
    progress: str | None = None
    query: str
    result: JobResultResponse | None = None
    error: str | None = None
    created_at: str


class JobCreateResponse(BaseModel):
    job_id: str
    status: str
    poll_url: str


class ErrorResponse(BaseModel):
    error: dict[str, str]
