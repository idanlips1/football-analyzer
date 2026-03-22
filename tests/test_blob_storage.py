"""Tests for BlobStorage — Azure Blob-backed StorageBackend."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from utils.storage import BlobStorage, StorageBackend


@pytest.fixture()
def mock_blob_service() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def blob_storage(tmp_path: Path, mock_blob_service: MagicMock) -> BlobStorage:
    return BlobStorage(
        connection_string="fake",
        container_videos="videos",
        container_pipeline="pipeline",
        container_highlights="highlights",
        temp_root=tmp_path,
        _blob_service_client=mock_blob_service,
    )


def test_implements_storage_backend(blob_storage: BlobStorage) -> None:
    assert isinstance(blob_storage, StorageBackend)


def test_write_json_uploads_to_blob_and_writes_locally(
    blob_storage: BlobStorage, mock_blob_service: MagicMock, tmp_path: Path
) -> None:
    data: dict[str, Any] = {"key": "value"}
    blob_storage.write_json("vid123", "metadata.json", data)

    mock_blob_service.get_container_client.assert_called_with("videos")
    container_client = mock_blob_service.get_container_client.return_value
    container_client.upload_blob.assert_called_once()
    call_args = container_client.upload_blob.call_args
    assert call_args[0][0] == "vid123/metadata.json"
    assert json.loads(call_args[0][1]) == data

    local_file = tmp_path / "vid123" / "metadata.json"
    assert local_file.exists()
    assert json.loads(local_file.read_text()) == data


def test_read_json_downloads_from_blob(
    blob_storage: BlobStorage, mock_blob_service: MagicMock
) -> None:
    expected: dict[str, Any] = {"events": []}
    blob_data = json.dumps(expected).encode()
    container_client = mock_blob_service.get_container_client.return_value
    blob_client = container_client.get_blob_client.return_value
    blob_client.download_blob.return_value.readall.return_value = blob_data

    result = blob_storage.read_json("vid123", "match_events.json")
    assert result == expected


def test_local_path_downloads_blob_to_temp(
    blob_storage: BlobStorage, mock_blob_service: MagicMock, tmp_path: Path
) -> None:
    blob_bytes = b"fake video content"
    container_client = mock_blob_service.get_container_client.return_value
    blob_client = container_client.get_blob_client.return_value
    blob_client.download_blob.return_value.readall.return_value = blob_bytes

    path = blob_storage.local_path("vid123", "video.mp4")
    assert path.exists()
    assert path.read_bytes() == blob_bytes


def test_local_path_uses_cache(
    blob_storage: BlobStorage, mock_blob_service: MagicMock, tmp_path: Path
) -> None:
    blob_bytes = b"fake video content"
    container_client = mock_blob_service.get_container_client.return_value
    blob_client = container_client.get_blob_client.return_value
    blob_client.download_blob.return_value.readall.return_value = blob_bytes

    path1 = blob_storage.local_path("vid123", "video.mp4")
    path2 = blob_storage.local_path("vid123", "video.mp4")
    assert path1 == path2
    assert blob_client.download_blob.call_count == 1


def test_workspace_path_returns_temp_dir(blob_storage: BlobStorage, tmp_path: Path) -> None:
    ws = blob_storage.workspace_path("vid123")
    assert ws.exists()
    assert ws.is_dir()
    assert "vid123" in str(ws)


def test_list_games_queries_pipeline_container(
    blob_storage: BlobStorage, mock_blob_service: MagicMock
) -> None:
    container_client = mock_blob_service.get_container_client.return_value
    blob1 = MagicMock()
    blob1.name = "vid123/aligned_events.json"
    blob2 = MagicMock()
    blob2.name = "vid123/game.json"
    blob3 = MagicMock()
    blob3.name = "vid456/aligned_events.json"
    container_client.list_blobs.return_value = [blob1, blob2, blob3]

    games = blob_storage.list_games()
    assert isinstance(games, list)
