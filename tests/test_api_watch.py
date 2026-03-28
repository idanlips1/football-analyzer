"""Tests for GET /watch/{job_id} HTML streaming endpoint."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from models.job import Job, JobResult, JobStatus
from utils.job_store import InMemoryJobStore


@pytest.fixture()
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture()
def client(store: InMemoryJobStore) -> Iterator[TestClient]:
    with patch("api.dependencies._store", store):
        from api.app import create_app

        app = create_app()
        yield TestClient(app)


def _completed_job(store: InMemoryJobStore) -> Job:
    job = Job(match_id="barcelona-2005", highlights_query="goals")
    job.status = JobStatus.COMPLETED
    job.result = JobResult(
        download_url="https://blob.example/highlights/vid.mp4?sig=abc",
        duration_seconds=120.0,
        clip_count=3,
        expires_at="2026-03-29T10:00:00+00:00",
    )
    store.create(job)
    return job


def test_watch_unknown_job_returns_404(client: TestClient) -> None:
    response = client.get("/watch/doesnotexist")
    assert response.status_code == 404


def test_watch_queued_job_returns_404(client: TestClient, store: InMemoryJobStore) -> None:
    job = Job(match_id="test", highlights_query="goals")
    store.create(job)
    response = client.get(f"/watch/{job.job_id}")
    assert response.status_code == 404


def test_watch_failed_job_returns_404(client: TestClient, store: InMemoryJobStore) -> None:
    job = Job(match_id="test", highlights_query="goals")
    job.status = JobStatus.FAILED
    job.error = "pipeline error"
    store.create(job)
    response = client.get(f"/watch/{job.job_id}")
    assert response.status_code == 404


def test_watch_completed_job_returns_html(client: TestClient, store: InMemoryJobStore) -> None:
    job = _completed_job(store)
    response = client.get(f"/watch/{job.job_id}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_watch_html_contains_video_tag_with_sas_url(
    client: TestClient, store: InMemoryJobStore
) -> None:
    job = _completed_job(store)
    response = client.get(f"/watch/{job.job_id}")
    body = response.text
    assert "<video" in body
    assert "https://blob.example/highlights/vid.mp4" in body


def test_watch_requires_no_api_key(client: TestClient, store: InMemoryJobStore) -> None:
    """Watch endpoint must be accessible without X-API-Key (opened in browser)."""
    job = _completed_job(store)
    with patch("api.app.API_KEYS", ["secret-key"]):
        from api.app import create_app

        app = create_app()
        with patch("api.dependencies._store", store):
            test_client = TestClient(app)
            response = test_client.get(f"/watch/{job.job_id}")
    assert response.status_code == 200
