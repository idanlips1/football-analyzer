"""Tests for job submission and polling endpoints."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from utils.job_queue import InMemoryQueue
from utils.job_store import InMemoryJobStore


@pytest.fixture()
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture()
def queue() -> InMemoryQueue:
    return InMemoryQueue()


@pytest.fixture()
def client(store: InMemoryJobStore, queue: InMemoryQueue) -> TestClient:
    with (
        patch("api.app.API_KEYS", ["test-key"]),
        patch("api.dependencies._store", store),
        patch("api.dependencies._queue", queue),
    ):
        from api.app import create_app
        app = create_app()
        yield TestClient(app)


HEADERS = {"X-API-Key": "test-key"}


def test_create_job(client: TestClient, queue: InMemoryQueue) -> None:
    response = client.post(
        "/api/v1/jobs",
        json={"query": "Liverpool vs City goals"},
        headers=HEADERS,
    )
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "queued"
    assert "job_id" in data
    assert "poll_url" in data
    msg = queue.receive()
    assert msg is not None
    assert msg.body["query"] == "Liverpool vs City goals"


def test_create_job_with_webhook(client: TestClient) -> None:
    response = client.post(
        "/api/v1/jobs",
        json={"query": "test", "webhook_url": "https://example.com/hook"},
        headers=HEADERS,
    )
    assert response.status_code == 202


def test_get_job_found(client: TestClient) -> None:
    response = client.post(
        "/api/v1/jobs", json={"query": "test"}, headers=HEADERS
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
    client.post("/api/v1/jobs", json={"query": "q1"}, headers=HEADERS)
    client.post("/api/v1/jobs", json={"query": "q2"}, headers=HEADERS)
    response = client.get("/api/v1/jobs", headers=HEADERS)
    assert len(response.json()["jobs"]) == 2
