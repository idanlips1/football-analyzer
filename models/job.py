"""Job data model for async highlights generation."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class JobResult:
    download_url: str
    duration_seconds: float
    clip_count: int
    expires_at: str  # ISO 8601

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobResult:
        return cls(**data)


@dataclass
class Job:
    job_id: str = field(default_factory=lambda: uuid4().hex[:12])
    match_id: str = ""
    highlights_query: str = "full match highlights"
    query: str = ""
    status: JobStatus = JobStatus.QUEUED
    progress: str | None = None
    result: JobResult | None = None
    error: str | None = None
    webhook_url: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["status"] = self.status.value
        if self.result:
            d["result"] = self.result.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Job:
        data = dict(data)
        data["status"] = JobStatus(data["status"])
        if data.get("result"):
            data["result"] = JobResult.from_dict(data["result"])
        valid_fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in valid_fields})
