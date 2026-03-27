"""Tests for job submission and polling endpoints."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from models.job import Job, JobResult, JobStatus
from utils.job_queue import InMemoryQueue
from utils.job_store import InMemoryJobStore

_VALID_MATCH = "istanbul-2005"
_OTHER_MATCH = "barcelona-psg-2017"


@pytest.fixture()
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture()
def queue() -> InMemoryQueue:
    return InMemoryQueue()


@pytest.fixture()
def mock_storage() -> MagicMock:
    storage = MagicMock()
    storage.list_games.return_value = [_VALID_MATCH, _OTHER_MATCH]
    return storage


@pytest.fixture()
def client(
    store: InMemoryJobStore,
    queue: InMemoryQueue,
    mock_storage: MagicMock,
) -> Iterator[TestClient]:
    with (
        patch("api.app.API_KEYS", ["test-key"]),
        patch("api.dependencies._store", store),
        patch("api.dependencies._queue", queue),
        patch("api.dependencies._storage", mock_storage),
    ):
        from api.app import create_app

        app = create_app()
        yield TestClient(app)


HEADERS = {"X-API-Key": "test-key"}


def test_list_matches(client: TestClient) -> None:
    response = client.get("/api/v1/matches", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert "matches" in data
    assert len(data["matches"]) >= 1
    ids = {m["match_id"] for m in data["matches"]}
    assert _VALID_MATCH in ids


def test_create_job(client: TestClient, queue: InMemoryQueue) -> None:
    response = client.post(
        "/api/v1/jobs",
        json={"match_id": _VALID_MATCH, "highlights_query": "goals"},
        headers=HEADERS,
    )
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "queued"
    assert "job_id" in data
    assert "poll_url" in data
    msg = queue.receive()
    assert msg is not None
    assert msg.body["match_id"] == _VALID_MATCH
    assert msg.body["highlights_query"] == "goals"


def test_create_job_unknown_match(client: TestClient) -> None:
    response = client.post(
        "/api/v1/jobs",
        json={"match_id": "not-a-real-id-xyz"},
        headers=HEADERS,
    )
    assert response.status_code == 400


def test_create_job_with_webhook(client: TestClient) -> None:
    response = client.post(
        "/api/v1/jobs",
        json={
            "match_id": _VALID_MATCH,
            "webhook_url": "https://example.com/hook",
        },
        headers=HEADERS,
    )
    assert response.status_code == 202


def test_get_job_found(client: TestClient) -> None:
    response = client.post(
        "/api/v1/jobs",
        json={"match_id": _VALID_MATCH},
        headers=HEADERS,
    )
    job_id = response.json()["job_id"]

    response = client.get(f"/api/v1/jobs/{job_id}", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["job_id"] == job_id
    assert response.json()["status"] == "queued"


def test_get_job_not_found(client: TestClient) -> None:
    response = client.get("/api/v1/jobs/nonexistent", headers=HEADERS)
    assert response.status_code == 404


def test_list_jobs_empty(client: TestClient) -> None:
    response = client.get("/api/v1/jobs", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["jobs"] == []


def test_list_jobs_with_results(client: TestClient) -> None:
    client.post(
        "/api/v1/jobs",
        json={"match_id": _VALID_MATCH},
        headers=HEADERS,
    )
    client.post(
        "/api/v1/jobs",
        json={"match_id": _OTHER_MATCH},
        headers=HEADERS,
    )
    response = client.get("/api/v1/jobs", headers=HEADERS)
    assert len(response.json()["jobs"]) == 2


def test_create_job_returns_cached_completed_job(
    client: TestClient,
    store: InMemoryJobStore,
) -> None:
    cached = Job(
        job_id="cachedjobid01",
        match_id=_VALID_MATCH,
        highlights_query="goals",
        query="label",
        status=JobStatus.COMPLETED,
    )
    cached.result = JobResult(
        download_url="https://example.com/old.mp4",
        duration_seconds=90.0,
        clip_count=3,
        expires_at="2099-01-01T00:00:00+00:00",
    )
    store.create(cached)

    response = client.post(
        "/api/v1/jobs",
        json={
            "match_id": _VALID_MATCH,
            "highlights_query": "goals",
        },
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["job_id"] == "cachedjobid01"
