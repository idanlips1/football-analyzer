"""Tests for ingest.py — all external I/O mocked."""

from __future__ import annotations

from typing import Any
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
            "fixture_id": None,
            "video_filename": "match.mp4",
            "source": "catalog:istanbul-2005",
            "duration_seconds": 5400.0,
            "events_snapshot": "istanbul-2005",
        }

        fake_transcription = {
            "kickoff_first_half": 330.0,
            "kickoff_second_half": 3420.0,
        }

        with (
            patch("ingest.ingest_local_catalog_match", return_value=fake_metadata),
            patch("pipeline.transcription.transcribe", return_value=fake_transcription),
            patch("ingest.fetch_match_events", return_value={"event_count": 0, "events": []}),
            patch("ingest.align_events", return_value={"event_count": 0, "events": []}),
            patch("ingest._pick_fixture_interactive") as mock_pick,
            patch("builtins.input", lambda _: "/tmp/fake.mp4"),
        ):
            _run_catalog_ingest(
                "istanbul-2005",
                tmp_storage,
                confirm_kickoffs_fn=lambda a, b: (330.0, 3420.0),
            )

        mock_pick.assert_not_called()
        game_data = tmp_storage.read_json("istanbul-2005", "game.json")
        assert game_data["home_team"] == "Liverpool"
        assert game_data["kickoff_first_half"] == 330.0
        assert game_data["video_id"] == "istanbul-2005"
        assert game_data["fixture_id"] is None

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


class TestIngestFetchesAndAlignsEvents:
    def test_fetch_match_events_called_after_kickoffs(self, tmp_storage: LocalStorage) -> None:
        from unittest.mock import patch

        from ingest import _run_catalog_ingest

        fake_metadata = {
            "video_id": "istanbul-2005",
            "home_team": "Liverpool",
            "away_team": "AC Milan",
            "competition": "UEFA Champions League",
            "season_label": "2004-05",
            "fixture_id": None,
            "video_filename": "match.mp4",
            "source": "catalog:istanbul-2005",
            "duration_seconds": 5400.0,
            "events_snapshot": "istanbul-2005",
        }
        fake_transcription = {
            "kickoff_first_half": 330.0,
            "kickoff_second_half": 3420.0,
        }
        fake_events = {"video_id": "istanbul-2005", "event_count": 2, "events": []}
        fake_aligned = {"video_id": "istanbul-2005", "event_count": 0, "events": []}

        with (
            patch("ingest.ingest_local_catalog_match", return_value=fake_metadata),
            patch("pipeline.transcription.transcribe", return_value=fake_transcription),
            patch("ingest.fetch_match_events", return_value=fake_events) as mock_fetch,
            patch("ingest.align_events", return_value=fake_aligned) as mock_align,
            patch("ingest._pick_fixture_interactive"),
            patch("builtins.input", lambda _: "/tmp/fake.mp4"),
        ):
            _run_catalog_ingest(
                "istanbul-2005",
                tmp_storage,
                confirm_kickoffs_fn=lambda a, b: (330.0, 3420.0),
            )

        mock_fetch.assert_called_once()
        mock_align.assert_called_once()
        _, align_kwargs = mock_align.call_args
        assert align_kwargs.get("force_recompute") is False
        assert align_kwargs.get("save_to_disk") is True

    def test_fixture_id_written_as_none_for_snapshot_match(self, tmp_storage: LocalStorage) -> None:
        from unittest.mock import patch

        from ingest import _run_catalog_ingest

        fake_metadata = {
            "video_id": "istanbul-2005",
            "home_team": "Liverpool",
            "away_team": "AC Milan",
            "competition": "UEFA Champions League",
            "season_label": "2004-05",
            "fixture_id": None,
            "video_filename": "match.mp4",
            "source": "catalog:istanbul-2005",
            "duration_seconds": 5400.0,
            "events_snapshot": "istanbul-2005",
        }
        fake_transcription = {"kickoff_first_half": 330.0, "kickoff_second_half": 3420.0}
        fake_events = {"video_id": "istanbul-2005", "event_count": 0, "events": []}
        fake_aligned = {"video_id": "istanbul-2005", "event_count": 0, "events": []}

        with (
            patch("ingest.ingest_local_catalog_match", return_value=fake_metadata),
            patch("pipeline.transcription.transcribe", return_value=fake_transcription),
            patch("ingest.fetch_match_events", return_value=fake_events),
            patch("ingest.align_events", return_value=fake_aligned),
            patch("ingest._pick_fixture_interactive"),
            patch("builtins.input", lambda _: "/tmp/fake.mp4"),
        ):
            _run_catalog_ingest(
                "istanbul-2005",
                tmp_storage,
                confirm_kickoffs_fn=lambda a, b: (330.0, 3420.0),
            )

        game_data = tmp_storage.read_json("istanbul-2005", "game.json")
        assert game_data["fixture_id"] is None

    def test_pick_fixture_not_called_for_snapshot_match(self, tmp_storage: LocalStorage) -> None:
        from unittest.mock import patch

        from ingest import _run_catalog_ingest

        fake_metadata = {
            "video_id": "istanbul-2005",
            "home_team": "Liverpool",
            "away_team": "AC Milan",
            "competition": "UEFA Champions League",
            "season_label": "2004-05",
            "fixture_id": None,
            "video_filename": "match.mp4",
            "source": "catalog:istanbul-2005",
            "duration_seconds": 5400.0,
            "events_snapshot": "istanbul-2005",
        }
        fake_transcription = {"kickoff_first_half": 330.0, "kickoff_second_half": 3420.0}

        with (
            patch("ingest.ingest_local_catalog_match", return_value=fake_metadata),
            patch("pipeline.transcription.transcribe", return_value=fake_transcription),
            patch("ingest.fetch_match_events", return_value={"event_count": 0, "events": []}),
            patch("ingest.align_events", return_value={"event_count": 0, "events": []}),
            patch("ingest._pick_fixture_interactive") as mock_pick,
            patch("builtins.input", lambda _: "/tmp/fake.mp4"),
        ):
            _run_catalog_ingest(
                "istanbul-2005",
                tmp_storage,
                confirm_kickoffs_fn=lambda a, b: (330.0, 3420.0),
            )

        mock_pick.assert_not_called()


class TestPickFixtureInteractive:
    _TEAMS_RESP_HOME = {"response": [{"team": {"id": 40, "name": "Liverpool"}}]}
    _TEAMS_RESP_AWAY = {"response": [{"team": {"id": 50, "name": "AC Milan"}}]}
    _H2H_MULTI = {
        "response": [
            {
                "fixture": {"id": 1001, "date": "2005-05-25"},
                "teams": {
                    "home": {"name": "Liverpool"},
                    "away": {"name": "AC Milan"},
                },
                "league": {"name": "UEFA Champions League"},
            },
            {
                "fixture": {"id": 1002, "date": "2007-05-23"},
                "teams": {
                    "home": {"name": "AC Milan"},
                    "away": {"name": "Liverpool"},
                },
                "league": {"name": "UEFA Champions League"},
            },
        ]
    }
    _H2H_SINGLE = {
        "response": [
            {
                "fixture": {"id": 1001, "date": "2005-05-25"},
                "teams": {
                    "home": {"name": "Liverpool"},
                    "away": {"name": "AC Milan"},
                },
                "league": {"name": "UEFA Champions League"},
            }
        ]
    }
    _H2H_EMPTY: dict[str, Any] = {"response": []}

    def _mock_api(self, responses: list[dict[str, Any]]) -> Any:
        import json
        from unittest.mock import MagicMock, patch

        call_count = [0]

        def fake_urlopen(req: Any) -> Any:
            idx = call_count[0]
            call_count[0] += 1
            body = json.dumps(responses[idx % len(responses)]).encode()
            mock_resp = MagicMock()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = body
            return mock_resp

        return patch("ingest.urllib.request.urlopen", side_effect=fake_urlopen)

    def test_zero_results_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ingest import _pick_fixture_interactive

        with (
            patch("ingest.API_FOOTBALL_KEY", "test-key"),
            self._mock_api([self._TEAMS_RESP_HOME, self._TEAMS_RESP_AWAY, self._H2H_EMPTY]),
        ):
            result = _pick_fixture_interactive("Liverpool", "AC Milan", "2004-05")
        assert result is None

    def test_single_result_confirmed_returns_fixture_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ingest import _pick_fixture_interactive

        monkeypatch.setattr("builtins.input", lambda _: "y")
        with (
            patch("ingest.API_FOOTBALL_KEY", "test-key"),
            self._mock_api([self._TEAMS_RESP_HOME, self._TEAMS_RESP_AWAY, self._H2H_SINGLE]),
        ):
            result = _pick_fixture_interactive("Liverpool", "AC Milan", "2004-05")
        assert result == 1001

    def test_single_result_rejected_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ingest import _pick_fixture_interactive

        monkeypatch.setattr("builtins.input", lambda _: "n")
        with (
            patch("ingest.API_FOOTBALL_KEY", "test-key"),
            self._mock_api([self._TEAMS_RESP_HOME, self._TEAMS_RESP_AWAY, self._H2H_SINGLE]),
        ):
            result = _pick_fixture_interactive("Liverpool", "AC Milan", "2004-05")
        assert result is None

    def test_multiple_results_operator_picks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ingest import _pick_fixture_interactive

        monkeypatch.setattr("builtins.input", lambda _: "1")
        with (
            patch("ingest.API_FOOTBALL_KEY", "test-key"),
            self._mock_api([self._TEAMS_RESP_HOME, self._TEAMS_RESP_AWAY, self._H2H_MULTI]),
        ):
            result = _pick_fixture_interactive("Liverpool", "AC Milan", "2004-05")
        assert result == 1001

    def test_multiple_results_operator_quits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ingest import _pick_fixture_interactive

        monkeypatch.setattr("builtins.input", lambda _: "q")
        with (
            patch("ingest.API_FOOTBALL_KEY", "test-key"),
            self._mock_api([self._TEAMS_RESP_HOME, self._TEAMS_RESP_AWAY, self._H2H_MULTI]),
        ):
            result = _pick_fixture_interactive("Liverpool", "AC Milan", "2004-05")
        assert result is None
