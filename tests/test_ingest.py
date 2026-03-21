"""Tests for ingest.py — all external I/O mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
        from ingest import _run_ingest

        fake_metadata = {
            "video_id": "vid1",
            "video_filename": "match.mp4",
            "duration_seconds": 5400.0,
            "fixture_id": 99,
        }
        fake_fixture_row = {
            "home_team": "Liverpool",
            "away_team": "Man City",
            "league": "Premier League",
            "date": "2024-10-26",
        }
        fake_match_events = {"events": [], "event_count": 0}
        fake_transcription = {
            "kickoff_first_half": 330.0,
            "kickoff_second_half": 3420.0,
            "utterances": [],
        }
        fake_aligned = {"video_id": "vid1", "event_count": 0, "events": []}

        mock_res = MagicMock()
        mock_res.fixture_id = 99
        mock_res.fixture_row = fake_fixture_row

        with (
            patch("ingest.download_and_save", return_value=fake_metadata),
            patch("ingest.resolve_fixture_for_video", return_value=mock_res),
            patch("ingest.fetch_match_events", return_value=fake_match_events),
            patch("ingest.transcribe", return_value=fake_transcription),
            patch("ingest.align_events", return_value=fake_aligned),
        ):
            _run_ingest(
                "https://www.youtube.com/watch?v=vid1",
                storage=tmp_storage,
                confirm_kickoffs_fn=lambda a, b: (330.0, 3420.0),
            )

        game_data = tmp_storage.read_json("vid1", "game.json")
        assert game_data["home_team"] == "Liverpool"
        assert game_data["kickoff_first_half"] == 330.0
        assert game_data["source"] == "https://www.youtube.com/watch?v=vid1"
        assert game_data["video_id"] == "vid1"

    def test_game_json_not_written_on_failure(self, tmp_storage: LocalStorage) -> None:
        from ingest import _run_ingest

        with (
            patch("ingest.download_and_save", side_effect=Exception("download failed")),
            pytest.raises(Exception, match="download failed"),
        ):
            _run_ingest(
                "https://www.youtube.com/watch?v=vid1",
                storage=tmp_storage,
                confirm_kickoffs_fn=lambda a, b: (0.0, 0.0),
            )

        assert not tmp_storage.local_path("vid1", "game.json").exists()
