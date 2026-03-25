"""Tests for API health check and auth middleware."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client() -> Iterator[TestClient]:
    from api.app import create_app

    with patch("api.app.API_KEYS", ["test-key-123"]):
        app = create_app()
        yield TestClient(app)


def test_health_no_auth_required(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_auth_required_without_key(client: TestClient) -> None:
    response = client.get("/api/v1/jobs")
    assert response.status_code == 401


def test_auth_required_with_wrong_key(client: TestClient) -> None:
    response = client.get("/api/v1/jobs", headers={"X-API-Key": "wrong"})
    assert response.status_code == 401


def test_auth_passes_with_valid_key(client: TestClient) -> None:
    response = client.get("/api/v1/jobs", headers={"X-API-Key": "test-key-123"})
    assert response.status_code == 200
