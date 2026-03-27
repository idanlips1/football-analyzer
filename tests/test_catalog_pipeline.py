"""Tests for run_catalog_pipeline — no API-Football calls at query time."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pipeline.catalog_pipeline import CatalogPipelineError


def _aligned_event(player: str = "Player A", minute: int = 10) -> dict[str, Any]:
    return {
        "event_type": "goal",
        "minute": minute,
        "extra_minute": None,
        "half": "1st Half",
        "player": player,
        "team": "Home",
        "score": "1-0",
        "detail": "Normal Goal",
        "estimated_video_ts": 900.0,
        "refined_video_ts": 895.0,
        "confidence": 0.9,
        "assist": None,
    }


def _game_dict() -> dict[str, Any]:
    return {
        "video_id": "istanbul-2005",
        "home_team": "Liverpool",
        "away_team": "AC Milan",
        "league": "UEFA Champions League",
        "date": "2004-05",
        "fixture_id": None,
        "video_filename": "match.mp4",
        "source": "catalog:istanbul-2005",
        "duration_seconds": 5400.0,
        "kickoff_first_half": 330.0,
        "kickoff_second_half": 3420.0,
    }


def _aligned_events_dict(events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"video_id": "istanbul-2005", "event_count": 1, "events": events or [_aligned_event()]}


class TestRunCatalogPipelineMissingData:
    def test_missing_aligned_events_raises(self, tmp_path: Path) -> None:
        from utils.storage import LocalStorage

        storage = LocalStorage(tmp_path)
        storage.write_json("istanbul-2005", "game.json", _game_dict())

        from pipeline.catalog_pipeline import run_catalog_pipeline

        with pytest.raises(CatalogPipelineError, match="Missing aligned events"):
            run_catalog_pipeline("istanbul-2005", "goals", storage)

    def test_empty_aligned_events_raises(self, tmp_path: Path) -> None:
        from utils.storage import LocalStorage

        storage = LocalStorage(tmp_path)
        storage.write_json("istanbul-2005", "game.json", _game_dict())
        storage.write_json("istanbul-2005", "aligned_events.json", {"events": []})

        from pipeline.catalog_pipeline import run_catalog_pipeline

        with pytest.raises(CatalogPipelineError, match="empty or stale"):
            run_catalog_pipeline("istanbul-2005", "goals", storage)


class TestRunCatalogPipelineNoApiCalls:
    def test_interpret_query_receives_player_name_list(self, tmp_path: Path) -> None:
        from utils.storage import LocalStorage

        storage = LocalStorage(tmp_path)
        storage.write_json("istanbul-2005", "game.json", _game_dict())
        storage.write_json(
            "istanbul-2005",
            "aligned_events.json",
            _aligned_events_dict(),
        )

        mock_hq = MagicMock()
        mock_hq.query_type.value = "full_summary"
        mock_hq.label = "goals"

        with (
            patch(
                "pipeline.query_interpreter.interpret_query",
                return_value=mock_hq,
            ) as mock_interp,
            patch("pipeline.event_filter.filter_events", return_value=[]),
            patch(
                "pipeline.clip_builder.build_highlights",
                return_value={
                    "highlights_path": "/tmp/h.mp4",
                    "clip_count": 0,
                    "total_duration_seconds": 0.0,
                },
            ),
        ):
            from pipeline.catalog_pipeline import run_catalog_pipeline

            run_catalog_pipeline("istanbul-2005", "goals", storage)

        call_args = mock_interp.call_args
        player_names_arg = call_args[0][2]
        assert isinstance(player_names_arg, list)
        assert all(isinstance(n, str) for n in player_names_arg)

    def test_player_names_extracted_from_aligned_events(self, tmp_path: Path) -> None:
        from utils.storage import LocalStorage

        storage = LocalStorage(tmp_path)
        storage.write_json("istanbul-2005", "game.json", _game_dict())
        events = [
            _aligned_event(player="Mohamed Salah"),
            {**_aligned_event(player="Darwin Nunez", minute=20), "assist": "Mohamed Salah"},
        ]
        storage.write_json(
            "istanbul-2005",
            "aligned_events.json",
            {"video_id": "istanbul-2005", "event_count": 2, "events": events},
        )

        captured: list[list[str]] = []

        def capture_interp(raw_query: str, game: Any, player_names: list[str]) -> MagicMock:
            captured.append(player_names)
            m = MagicMock()
            m.query_type.value = "full_summary"
            m.label = "test"
            return m

        with (
            patch("pipeline.query_interpreter.interpret_query", side_effect=capture_interp),
            patch("pipeline.event_filter.filter_events", return_value=[]),
            patch(
                "pipeline.clip_builder.build_highlights",
                return_value={
                    "highlights_path": "/tmp/h.mp4",
                    "clip_count": 0,
                    "total_duration_seconds": 0.0,
                },
            ),
        ):
            from pipeline.catalog_pipeline import run_catalog_pipeline

            run_catalog_pipeline("istanbul-2005", "goals", storage)

        assert "Mohamed Salah" in captured[0]
        assert "Darwin Nunez" in captured[0]
