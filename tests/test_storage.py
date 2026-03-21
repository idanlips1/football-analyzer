"""Tests for LocalStorage."""

from __future__ import annotations

from pathlib import Path

import pytest

from utils.storage import LocalStorage


@pytest.fixture()
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(root=tmp_path)


class TestLocalStorage:
    def test_write_and_read_json(self, storage: LocalStorage) -> None:
        storage.write_json("vid1", "data.json", {"key": "value"})
        result = storage.read_json("vid1", "data.json")
        assert result == {"key": "value"}

    def test_write_creates_directory(self, storage: LocalStorage, tmp_path: Path) -> None:
        storage.write_json("vid1", "data.json", {})
        assert (tmp_path / "vid1").is_dir()

    def test_local_path_returns_path(self, storage: LocalStorage, tmp_path: Path) -> None:
        p = storage.local_path("vid1", "file.mp4")
        assert p == tmp_path / "vid1" / "file.mp4"

    def test_workspace_path_creates_dir(self, storage: LocalStorage, tmp_path: Path) -> None:
        ws = storage.workspace_path("vid1")
        assert ws == tmp_path / "vid1"
        assert ws.is_dir()

    def test_list_games_empty_workspace(self, storage: LocalStorage) -> None:
        assert storage.list_games() == []

    def test_list_games_returns_only_complete_games(
        self, storage: LocalStorage, tmp_path: Path
    ) -> None:
        # Complete game: both files present
        (tmp_path / "vid_complete").mkdir()
        (tmp_path / "vid_complete" / "game.json").write_text("{}")
        (tmp_path / "vid_complete" / "aligned_events.json").write_text("{}")

        # Partial game: only aligned_events.json
        (tmp_path / "vid_partial").mkdir()
        (tmp_path / "vid_partial" / "aligned_events.json").write_text("{}")

        # No files
        (tmp_path / "vid_empty").mkdir()

        result = storage.list_games()
        assert result == ["vid_complete"]

    def test_list_games_game_json_only_excluded(
        self, storage: LocalStorage, tmp_path: Path
    ) -> None:
        # Partial ingest: game.json written but aligned_events.json not yet — must be invisible
        (tmp_path / "vid_partial2").mkdir()
        (tmp_path / "vid_partial2" / "game.json").write_text("{}")
        assert storage.list_games() == []

    def test_list_games_missing_root(self, tmp_path: Path) -> None:
        storage = LocalStorage(root=tmp_path / "nonexistent")
        assert storage.list_games() == []

    def test_read_json_file_not_found_raises(self, storage: LocalStorage) -> None:
        from utils.storage import StorageError

        with pytest.raises(StorageError, match="not found"):
            storage.read_json("vid1", "missing.json")
