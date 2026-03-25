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
    job = Job(match_id="istanbul-2005", highlights_query="goals", query="label")
    store.create(job)

    mock_result = {
        "highlights_path": "/tmp/highlights.mp4",
        "clip_count": 5,
        "total_duration_seconds": 120.0,
        "total_duration_display": "2:00",
        "video_id": "abc123",
    }

    with (
        patch("worker.runner._run_pipeline", return_value=mock_result),
        patch("worker.runner.deliver_webhook"),
    ):
        process_job(
            job_id=job.job_id,
            match_id=job.match_id,
            highlights_query=job.highlights_query,
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
    job = Job(match_id="istanbul-2005", highlights_query="x", query="")
    store.create(job)

    with (
        patch("worker.runner._run_pipeline", side_effect=RuntimeError("download failed")),
        patch("worker.runner.deliver_webhook"),
    ):
        process_job(
            job_id=job.job_id,
            match_id=job.match_id,
            highlights_query=job.highlights_query,
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
    job = Job(match_id="istanbul-2005", highlights_query="test", query="")
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
        "video_id": "abc123",
    }

    with (
        patch("worker.runner._run_pipeline", return_value=mock_result),
        patch("worker.runner.deliver_webhook"),
    ):
        process_job(
            job_id=job.job_id,
            match_id=job.match_id,
            highlights_query=job.highlights_query,
            webhook_url=None,
            store=store,
            storage=mock_storage,
        )

    assert len(progress_log) > 0


def test_process_job_uses_azure_url_and_cleans_video_temp(
    store: InMemoryJobStore,
) -> None:
    class FakeAzureStorage:
        def __init__(self) -> None:
            self.cleaned_video_id: str | None = None

        def upload_highlights(self, video_id: str, query_hash: str, local_file: Path) -> str:
            assert video_id == "vid777"
            assert local_file.name == "h.mp4"
            assert query_hash
            return f"{video_id}/{query_hash}.mp4"

        def generate_sas_url(self, blob_name: str, expiry_hours: int = 24) -> str:
            assert blob_name.startswith("vid777/")
            assert expiry_hours > 0
            return f"https://blob.example/{blob_name}?sig=test"

        def cleanup_temp(self, video_id: str) -> None:
            self.cleaned_video_id = video_id

    storage = FakeAzureStorage()
    job = Job(match_id="istanbul-2005", highlights_query="goals", query="")
    store.create(job)

    mock_result = {
        "highlights_path": "/tmp/h.mp4",
        "clip_count": 2,
        "total_duration_seconds": 45.0,
        "total_duration_display": "0:45",
        "video_id": "vid777",
    }

    with (
        patch("worker.runner._run_pipeline", return_value=mock_result),
        patch("worker.runner.deliver_webhook"),
    ):
        process_job(
            job_id=job.job_id,
            match_id=job.match_id,
            highlights_query=job.highlights_query,
            webhook_url=None,
            store=store,
            storage=storage,  # type: ignore[arg-type]
        )

    updated = store.get(job.job_id)
    assert updated is not None
    assert updated.result is not None
    assert updated.result.download_url.startswith("https://blob.example/vid777/")
    assert updated.result.expires_at != ""
    assert storage.cleaned_video_id == "vid777"
