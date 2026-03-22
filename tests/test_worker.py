"""Tests for worker runner — queue consumer + pipeline execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from models.job import Job, JobStatus
from utils.job_queue import InMemoryQueue
from utils.job_store import InMemoryJobStore
from worker.runner import process_job


@pytest.fixture()
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture()
def queue() -> InMemoryQueue:
    return InMemoryQueue()


@pytest.fixture()
def mock_storage(tmp_path: Path) -> Any:
    from utils.storage import LocalStorage

    return LocalStorage(tmp_path / "workspace")


def test_process_job_success(
    store: InMemoryJobStore,
    mock_storage: Any,
) -> None:
    job = Job(query="Liverpool vs City goals")
    store.create(job)

    mock_result = {
        "highlights_path": "/tmp/highlights.mp4",
        "clip_count": 5,
        "total_duration_seconds": 120.0,
        "total_duration_display": "2:00",
    }

    with (
        patch("worker.runner._run_pipeline", return_value=mock_result),
        patch("worker.runner.deliver_webhook"),
    ):
        process_job(
            job_id=job.job_id,
            query=job.query,
            webhook_url=None,
            store=store,
            storage=mock_storage,
        )

    updated = store.get(job.job_id)
    assert updated is not None
    assert updated.status == JobStatus.COMPLETED
    assert updated.result is not None


def test_process_job_failure(
    store: InMemoryJobStore,
    mock_storage: Any,
) -> None:
    job = Job(query="bad query")
    store.create(job)

    with (
        patch("worker.runner._run_pipeline", side_effect=RuntimeError("download failed")),
        patch("worker.runner.deliver_webhook"),
    ):
        process_job(
            job_id=job.job_id,
            query=job.query,
            webhook_url=None,
            store=store,
            storage=mock_storage,
        )

    updated = store.get(job.job_id)
    assert updated is not None
    assert updated.status == JobStatus.FAILED
    assert updated.error is not None
    assert "download failed" in updated.error


def test_process_job_updates_progress(
    store: InMemoryJobStore,
    mock_storage: Any,
) -> None:
    job = Job(query="test")
    store.create(job)

    progress_log: list[str] = []
    original_update = store.update

    def tracking_update(job_id: str, **fields: Any) -> None:
        if "progress" in fields:
            progress_log.append(fields["progress"])
        original_update(job_id, **fields)

    store.update = tracking_update  # type: ignore[assignment]

    mock_result = {
        "highlights_path": "/tmp/h.mp4",
        "clip_count": 1,
        "total_duration_seconds": 30.0,
        "total_duration_display": "0:30",
    }

    with (
        patch("worker.runner._run_pipeline", return_value=mock_result),
        patch("worker.runner.deliver_webhook"),
    ):
        process_job(
            job_id=job.job_id,
            query=job.query,
            webhook_url=None,
            store=store,
            storage=mock_storage,
        )

    assert len(progress_log) > 0
