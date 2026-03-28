"""Tests for GET /api/v1/matches — enriched catalog response."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

_CATALOG = [
    {
        "match_id": "liv-v-mad-ucl-2024",
        "title": "Liverpool vs Real Madrid",
        "home_team": "Liverpool",
        "away_team": "Real Madrid",
        "competition": "Champions League",
        "season_label": "2024",
    },
]


@pytest.fixture()
def mock_storage() -> MagicMock:
    storage = MagicMock()
    storage.list_games.return_value = ["liv-v-mad-ucl-2024"]
    return storage


@pytest.fixture()
def client(mock_storage: MagicMock) -> Iterator[TestClient]:
    with (
        patch("api.app.API_KEYS", new=set()),
        patch("api.dependencies._storage", mock_storage),
        patch("api.routes.catalog.list_matches", return_value=_CATALOG),
    ):
        from api.app import create_app

        yield TestClient(create_app())


def test_matches_returns_enriched_catalog_entries(client: TestClient) -> None:
    """Matches endpoint returns catalog metadata, not just IDs."""
    resp = client.get("/api/v1/matches")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["matches"]) == 1
    match = data["matches"][0]
    assert match["match_id"] == "liv-v-mad-ucl-2024"
    assert match["home_team"] == "Liverpool"
    assert match["competition"] == "Champions League"


def test_matches_filters_out_non_ingested(mock_storage: MagicMock) -> None:
    """Catalog entries without corresponding storage are excluded."""
    mock_storage.list_games.return_value = []  # nothing in storage
    with (
        patch("api.app.API_KEYS", new=set()),
        patch("api.dependencies._storage", mock_storage),
        patch("api.routes.catalog.list_matches", return_value=_CATALOG),
    ):
        from api.app import create_app

        client = TestClient(create_app())
        resp = client.get("/api/v1/matches")

    assert resp.status_code == 200
    assert resp.json()["matches"] == []
