"""Tests for main.py — query REPL helpers."""

from __future__ import annotations

import pytest

from models.game import GameState
from utils.storage import LocalStorage


def _make_game(video_id: str = "vid1") -> GameState:
    return GameState(
        video_id=video_id,
        home_team="Liverpool",
        away_team="Man City",
        league="Premier League",
        date="2024-10-26",
        fixture_id=1,
        video_filename="match.mp4",
        source=f"https://www.youtube.com/watch?v={video_id}",
        duration_seconds=5400.0,
        kickoff_first_half=330.0,
        kickoff_second_half=3420.0,
    )


class TestDisplayGameList:
    def test_formats_game_line(self, capsys: pytest.CaptureFixture[str]) -> None:
        from main import _display_game_list

        _display_game_list([_make_game()])
        captured = capsys.readouterr()
        assert "Liverpool" in captured.out
        assert "Man City" in captured.out
        assert "2024-10-26" in captured.out

    def test_multiple_games_numbered(self, capsys: pytest.CaptureFixture[str]) -> None:
        from main import _display_game_list

        games = [_make_game("vid1"), _make_game("vid2")]
        _display_game_list(games)
        out = capsys.readouterr().out
        assert "[1]" in out
        assert "[2]" in out


class TestNoGamesReady:
    def test_exits_cleanly_when_no_games(
        self,
        tmp_storage: LocalStorage,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from main import run

        monkeypatch.setattr("main._make_storage", lambda: tmp_storage)
        run()
        out = capsys.readouterr().out
        assert "No ingested games" in out


class TestLoadAlignedEvents:
    def test_loads_empty_events(self, tmp_storage: LocalStorage) -> None:
        from main import _load_aligned_events

        game = _make_game("vid1")
        tmp_storage.write_json("vid1", "aligned_events.json", {"events": []})
        result = _load_aligned_events(game, tmp_storage)
        assert result == []
