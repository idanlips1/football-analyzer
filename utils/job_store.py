"""Job store abstraction — in-memory and Azure Table implementations."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol, runtime_checkable

from models.job import Job, JobResult, JobStatus

_JOB_ID_RE = re.compile(r"^[0-9a-f]{1,32}$")


@runtime_checkable
class JobStore(Protocol):
    def create(self, job: Job) -> None: ...
    def get(self, job_id: str) -> Job | None: ...
    def update(self, job_id: str, **fields: Any) -> None: ...
    def list_recent(self, limit: int = 20) -> list[Job]: ...


class InMemoryJobStore:
    """In-memory job store for local development."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self, job: Job) -> None:
        self._jobs[job.job_id] = job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def update(self, job_id: str, **fields: Any) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        for key, value in fields.items():
            if key == "status":
                value = JobStatus(value)
            setattr(job, key, value)

    def list_recent(self, limit: int = 20) -> list[Job]:
        jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]


class AzureTableJobStore:
    """Azure Table Storage-backed job store."""

    def __init__(
        self,
        connection_string: str,
        table_name: str = "jobs",
        _table_client: Any | None = None,
    ) -> None:
        if _table_client is not None:
            self._table = _table_client
        else:
            from azure.data.tables import TableServiceClient

            service = TableServiceClient.from_connection_string(connection_string)
            self._table = service.get_table_client(table_name)

    def _to_entity(self, job: Job) -> dict[str, Any]:
        return {
            "PartitionKey": job.created_at[:10],
            "RowKey": job.job_id,
            "match_id": job.match_id or "",
            "highlights_query": job.highlights_query or "",
            "query": job.query,
            "status": job.status.value,
            "progress": job.progress or "",
            "result": json.dumps(job.result.to_dict()) if job.result else "",
            "error": job.error or "",
            "webhook_url": job.webhook_url or "",
            "created_at": job.created_at,
        }

    def _from_entity(self, entity: dict[str, Any]) -> Job:
        result = None
        if entity.get("result"):
            result = JobResult.from_dict(json.loads(entity["result"]))
        return Job(
            job_id=entity["RowKey"],
            match_id=entity.get("match_id") or "",
            highlights_query=entity.get("highlights_query") or "full match highlights",
            query=entity.get("query", ""),
            status=JobStatus(entity["status"]),
            progress=entity.get("progress") or None,
            result=result,
            error=entity.get("error") or None,
            webhook_url=entity.get("webhook_url") or None,
            created_at=entity.get("created_at", ""),
        )

    def create(self, job: Job) -> None:
        self._table.create_entity(self._to_entity(job))

    def get(self, job_id: str) -> Job | None:
        if not _JOB_ID_RE.match(job_id):
            return None
        entities = list(self._table.query_entities(f"RowKey eq '{job_id}'", results_per_page=1))
        if not entities:
            return None
        return self._from_entity(entities[0])

    def update(self, job_id: str, **fields: Any) -> None:
        job = self.get(job_id)
        if job is None:
            return
        for key, value in fields.items():
            if key == "status":
                value = JobStatus(value)
            setattr(job, key, value)
        self._table.upsert_entity(self._to_entity(job))

    def list_recent(self, limit: int = 20) -> list[Job]:
        entities = list(self._table.query_entities("", results_per_page=limit))
        jobs = [self._from_entity(e) for e in entities]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)[:limit]
