"""Pydantic request/response models for the API."""

from __future__ import annotations

from pydantic import BaseModel, Field, HttpUrl, field_validator


class JobCreateRequest(BaseModel):
    match_id: str = Field(
        ...,
        pattern=r"^[a-z0-9][a-z0-9-]{0,62}$",
        description="Curated catalog id (see GET /api/v1/matches)",
    )
    highlights_query: str = Field(
        "full match highlights",
        max_length=500,
        description="Natural-language highlights request for query interpreter",
    )
    webhook_url: HttpUrl | None = None
    kickoff_first_half: float | None = Field(None, ge=0)
    kickoff_second_half: float | None = Field(None, ge=0)

    @field_validator("webhook_url")
    @classmethod
    def reject_private_urls(cls, v: HttpUrl | None) -> HttpUrl | None:
        if v is None:
            return v
        host = str(v.host or "")
        if host in ("localhost", "127.0.0.1", "0.0.0.0") or host.startswith("169.254."):
            msg = "webhook_url must not point to localhost or link-local addresses"
            raise ValueError(msg)
        return v


class JobResultResponse(BaseModel):
    download_url: str
    duration_seconds: float
    clip_count: int
    expires_at: str


class JobResponse(BaseModel):
    job_id: str
    status: str
    progress: str | None = None
    match_id: str = ""
    highlights_query: str = ""
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
