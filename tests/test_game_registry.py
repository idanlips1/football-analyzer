"""Tests for GameRegistry."""

from __future__ import annotations

from models.game import GameState
from utils.game_registry import GameRegistry
from utils.storage import LocalStorage


def _write_game(storage: LocalStorage, video_id: str, **kwargs: object) -> GameState:
    """Helper: write a game.json and empty aligned_events.json to storage."""
    defaults = dict(
        video_id=video_id,
        home_team="Liverpool",
        away_team="Man City",
        league="Premier League",
        date="2024-10-26",
        fixture_id=12345,
        video_filename="match.mp4",
        source=f"https://www.youtube.com/watch?v={video_id}",
        duration_seconds=5400.0,
        kickoff_first_half=330.0,
        kickoff_second_half=3420.0,
    )
    defaults.update(kwargs)
    gs = GameState(**defaults)  # type: ignore[arg-type]
    storage.write_json(video_id, "game.json", gs.to_dict())
    storage.write_json(video_id, "aligned_events.json", {"events": [], "event_count": 0})
    return gs


class TestGameRegistry:
    def test_list_ready_empty(self, tmp_storage: LocalStorage) -> None:
        registry = GameRegistry(tmp_storage)
        assert registry.list_ready() == []

    def test_list_ready_returns_game_state(self, tmp_storage: LocalStorage) -> None:
        expected = _write_game(tmp_storage, "vid1")
        registry = GameRegistry(tmp_storage)
        result = registry.list_ready()
        assert len(result) == 1
        assert result[0] == expected

    def test_list_ready_excludes_partial_ingest(self, tmp_storage: LocalStorage) -> None:
        # Write game.json but NOT aligned_events.json
        gs = GameState(
            video_id="partial",
            home_team="A",
            away_team="B",
            league="L",
            date="2024-01-01",
            fixture_id=1,
            video_filename="v.mp4",
            source="https://www.youtube.com/watch?v=partial",
            duration_seconds=100.0,
            kickoff_first_half=10.0,
            kickoff_second_half=60.0,
        )
        tmp_storage.write_json("partial", "game.json", gs.to_dict())
        # No aligned_events.json written
        registry = GameRegistry(tmp_storage)
        assert registry.list_ready() == []

    def test_list_ready_multiple_games(self, tmp_storage: LocalStorage) -> None:
        _write_game(tmp_storage, "vid1")
        _write_game(tmp_storage, "vid2")
        registry = GameRegistry(tmp_storage)
        assert len(registry.list_ready()) == 2
