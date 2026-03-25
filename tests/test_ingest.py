"""Tests for ingest.py — all external I/O mocked."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from utils.storage import LocalStorage


class TestParseTimestamp:
    def test_mmss_format(self) -> None:
        from ingest import _parse_timestamp

        assert _parse_timestamp("5:30") == 330.0

    def test_seconds_format(self) -> None:
        from ingest import _parse_timestamp

        assert _parse_timestamp("330") == 330.0

    def test_invalid_returns_none(self) -> None:
        from ingest import _parse_timestamp

        assert _parse_timestamp("abc") is None

    def test_invalid_mmss_returns_none(self) -> None:
        from ingest import _parse_timestamp

        assert _parse_timestamp("5:xx") is None


class TestConfirmKickoffsInteractive:
    def test_auto_detected_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ingest import _confirm_kickoffs_interactive

        monkeypatch.setattr("builtins.input", lambda _: "y")
        first, second = _confirm_kickoffs_interactive(330.0, 3420.0)
        assert first == 330.0
        assert second == 3420.0

    def test_auto_detected_rejected_manual_entry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ingest import _confirm_kickoffs_interactive

        responses = iter(["n", "5:30", "n", "57:00"])
        monkeypatch.setattr("builtins.input", lambda _: next(responses))
        first, second = _confirm_kickoffs_interactive(330.0, 3420.0)
        assert first == 330.0  # 5:30 = 330s
        assert second == 3420.0  # 57:00 = 3420s

    def test_none_detected_requires_manual_entry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ingest import _confirm_kickoffs_interactive

        responses = iter(["2:00", "48:00"])
        monkeypatch.setattr("builtins.input", lambda _: next(responses))
        first, second = _confirm_kickoffs_interactive(None, None)
        assert first == 120.0
        assert second == 2880.0


class TestIngestWritesGameJson:
    def test_game_json_written_after_successful_ingest(self, tmp_storage: LocalStorage) -> None:
        from ingest import _run_catalog_ingest

        fake_metadata = {
            "video_id": "istanbul-2005",
            "home_team": "Liverpool",
            "away_team": "AC Milan",
            "competition": "UEFA Champions League",
            "season_label": "2004-05",
            "fixture_id": 0,
            "video_filename": "match.mp4",
            "source": "catalog:istanbul-2005",
            "duration_seconds": 5400.0,
        }

        fake_transcription = {
            "kickoff_first_half": 330.0,
            "kickoff_second_half": 3420.0,
        }

        with (
            patch("ingest.ingest_local_catalog_match", return_value=fake_metadata),
            patch("ingest.transcribe", return_value=fake_transcription),
            patch("builtins.input", lambda _: "/tmp/fake.mp4"),
        ):
            _run_catalog_ingest(
                "istanbul-2005",
                tmp_storage,
                confirm_kickoffs_fn=lambda a, b: (330.0, 3420.0),
            )

        game_data = tmp_storage.read_json("istanbul-2005", "game.json")
        assert game_data["home_team"] == "Liverpool"
        assert game_data["kickoff_first_half"] == 330.0
        assert game_data["video_id"] == "istanbul-2005"

    def test_game_json_not_written_on_failure(self, tmp_storage: LocalStorage) -> None:
        from ingest import _run_catalog_ingest

        with (
            patch(
                "ingest.ingest_local_catalog_match",
                side_effect=Exception("copy failed"),
            ),
            patch("builtins.input", return_value="/tmp/fake.mp4"),
            pytest.raises(Exception, match="copy failed"),
        ):
            _run_catalog_ingest(
                "istanbul-2005",
                tmp_storage,
                confirm_kickoffs_fn=lambda a, b: (0.0, 0.0),
            )

        assert not tmp_storage.local_path("istanbul-2005", "game.json").exists()
