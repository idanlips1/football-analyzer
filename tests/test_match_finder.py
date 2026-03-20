"""Tests for pipeline.match_finder — YouTube search and API-Football fixture lookup."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pipeline.match_finder import (
    MatchFinderError,
    download_and_save,
    find_match,
    is_url,
    parse_teams_from_video_title,
    resolve_fixture_for_video,
    search_fixtures,
    search_youtube,
)

# ── is_url ──────────────────────────────────────────────────────────────────


class TestIsUrl:
    def test_http_url(self) -> None:
        assert is_url("http://example.com") is True

    def test_https_url(self) -> None:
        assert is_url("https://www.youtube.com/watch?v=abc") is True

    def test_plain_text(self) -> None:
        assert is_url("liverpool vs arsenal") is False

    def test_empty_string(self) -> None:
        assert is_url("") is False

    def test_url_like_but_no_scheme(self) -> None:
        assert is_url("www.youtube.com/watch?v=abc") is False


# ── search_youtube ──────────────────────────────────────────────────────────


class TestSearchYouTube:
    @staticmethod
    def _yt_entries() -> list[dict[str, Any]]:
        return [
            {
                "id": "vid1",
                "title": "Liverpool vs Arsenal Full Match",
                "webpage_url": "https://www.youtube.com/watch?v=vid1",
                "duration": 5700,
            },
            {
                "id": "vid2",
                "title": "Liverpool vs Arsenal Highlights",
                "webpage_url": "https://www.youtube.com/watch?v=vid2",
                "duration": 600,
            },
            {
                "id": "vid3",
                "title": "Liverpool vs Arsenal Full Match HD",
                "webpage_url": "https://www.youtube.com/watch?v=vid3",
                "duration": 5400,
            },
        ]

    @patch("pipeline.match_finder.yt_dlp.YoutubeDL")
    def test_returns_filtered_and_sorted(self, mock_ydl_cls: MagicMock) -> None:
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {"entries": self._yt_entries()}

        results = search_youtube("liverpool vs arsenal")

        assert len(results) == 2
        assert results[0]["video_id"] == "vid1"
        assert results[0]["duration_seconds"] == 5700
        assert results[1]["video_id"] == "vid3"

    @patch("pipeline.match_finder.yt_dlp.YoutubeDL")
    def test_filters_short_videos(self, mock_ydl_cls: MagicMock) -> None:
        short_entries = [
            {
                "id": "short1",
                "title": "Highlights 10min",
                "webpage_url": "https://www.youtube.com/watch?v=short1",
                "duration": 600,
            },
        ]
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {"entries": short_entries}

        results = search_youtube("some match")
        assert results == []

    @patch("pipeline.match_finder.yt_dlp.YoutubeDL")
    def test_handles_no_entries(self, mock_ydl_cls: MagicMock) -> None:
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {"entries": []}

        results = search_youtube("nonexistent match")
        assert results == []

    @patch("pipeline.match_finder.yt_dlp.YoutubeDL")
    def test_handles_none_entries(self, mock_ydl_cls: MagicMock) -> None:
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = None

        results = search_youtube("nonexistent match")
        assert results == []

    @patch("pipeline.match_finder.yt_dlp.YoutubeDL")
    def test_respects_max_results(self, mock_ydl_cls: MagicMock) -> None:
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {"entries": self._yt_entries()}

        search_youtube("liverpool vs arsenal", max_results=3)

        call_args = mock_ydl.extract_info.call_args
        assert "ytsearch3:" in call_args[0][0]


# ── search_fixtures ─────────────────────────────────────────────────────────


class TestSearchFixtures:
    @staticmethod
    def _team_response(team_id: int, name: str) -> bytes:
        return json.dumps({"response": [{"team": {"id": team_id, "name": name}}]}).encode()

    @staticmethod
    def _fixtures_response() -> bytes:
        return json.dumps(
            {
                "response": [
                    {
                        "fixture": {
                            "id": 12345,
                            "date": "2025-12-01T20:00:00+00:00",
                        },
                        "league": {"id": 39, "name": "Premier League"},
                        "teams": {
                            "home": {"id": 40, "name": "Liverpool"},
                            "away": {"id": 42, "name": "Arsenal"},
                        },
                        "goals": {"home": 2, "away": 1},
                    },
                ]
            }
        ).encode()

    @staticmethod
    def _ctx(data: bytes) -> MagicMock:
        """Wrap bytes in a mock that works as a context manager for urlopen."""
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=data)))
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    @patch("pipeline.match_finder.urllib.request.urlopen")
    def test_returns_fixtures(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = [
            self._ctx(self._team_response(40, "Liverpool")),
            self._ctx(self._team_response(42, "Arsenal")),
            self._ctx(self._fixtures_response()),
        ]

        results = search_fixtures("Liverpool", "Arsenal")

        assert len(results) == 1
        assert results[0]["fixture_id"] == 12345
        assert results[0]["home_team"] == "Liverpool"
        assert results[0]["away_team"] == "Arsenal"

    @patch("pipeline.match_finder.urllib.request.urlopen")
    def test_returns_empty_on_api_error(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = Exception("Connection refused")

        results = search_fixtures("Liverpool", "Arsenal")
        assert results == []

    @patch("pipeline.match_finder.urllib.request.urlopen")
    def test_returns_empty_when_no_team_found(self, mock_urlopen: MagicMock) -> None:
        empty_resp = json.dumps({"response": []}).encode()
        mock_urlopen.return_value = self._ctx(empty_resp)

        results = search_fixtures("Nonexistent FC", "Arsenal")
        assert results == []

    @patch("pipeline.match_finder.urllib.request.urlopen")
    def test_excludes_fixtures_when_opponent_not_team2(self, mock_urlopen: MagicMock) -> None:
        wrong_opponent = json.dumps(
            {
                "response": [
                    {
                        "fixture": {"id": 999, "date": "2025-12-01T20:00:00+00:00"},
                        "league": {"id": 39, "name": "Premier League"},
                        "teams": {
                            "home": {"id": 40, "name": "Liverpool"},
                            "away": {"id": 99, "name": "Chelsea"},
                        },
                        "goals": {"home": 1, "away": 0},
                    },
                ]
            }
        ).encode()
        mock_urlopen.side_effect = [
            self._ctx(self._team_response(40, "Liverpool")),
            self._ctx(self._team_response(42, "Arsenal")),
            self._ctx(wrong_opponent),
        ]

        results = search_fixtures("Liverpool", "Arsenal")
        assert results == []

    @patch("pipeline.match_finder.urllib.request.urlopen")
    def test_passes_season_to_api(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = [
            self._ctx(self._team_response(40, "Liverpool")),
            self._ctx(self._team_response(42, "Arsenal")),
            self._ctx(self._fixtures_response()),
        ]

        search_fixtures("Liverpool", "Arsenal", season=2023)

        fixtures_call = mock_urlopen.call_args_list[2]
        assert "season=2023" in fixtures_call[0][0].full_url


# ── find_match ──────────────────────────────────────────────────────────────


class TestFindMatch:
    @staticmethod
    def _fake_download(_url: str, workspace: Path) -> Path:
        video = workspace / "fake_match.mp4"
        video.write_bytes(b"\x00" * 512)
        return video

    @patch("pipeline.match_finder._extract_video_id", return_value="abc123")
    @patch("pipeline.match_finder.get_video_duration", return_value=5400.0)
    def test_url_input_downloads_and_returns_metadata(
        self,
        _mock_dur: MagicMock,
        _mock_id: MagicMock,
        tmp_workspace: Path,
    ) -> None:
        with patch(
            "pipeline.match_finder._download_video",
            side_effect=lambda _url, w: self._fake_download(_url, w),
        ):
            result = find_match("https://www.youtube.com/watch?v=abc123")

        assert result["video_id"] == "abc123"
        assert result["duration_seconds"] == 5400.0
        assert result["fixture_id"] is None
        meta_path = tmp_workspace / "abc123" / "metadata.json"
        assert meta_path.exists()

    @patch("pipeline.match_finder._extract_video_id", return_value="cached1")
    def test_url_input_returns_cached(
        self,
        _mock_id: MagicMock,
        tmp_workspace: Path,
    ) -> None:
        ws = tmp_workspace / "cached1"
        ws.mkdir()
        cached_meta = {
            "video_id": "cached1",
            "source": "https://www.youtube.com/watch?v=cached1",
            "video_filename": "match.mp4",
            "duration_seconds": 5400.0,
            "workspace": str(ws),
            "fixture_id": None,
        }
        (ws / "metadata.json").write_text(json.dumps(cached_meta))

        result = find_match("https://www.youtube.com/watch?v=cached1")

        assert result == cached_meta

    @patch("pipeline.match_finder.search_youtube")
    def test_text_input_returns_search_results(
        self,
        mock_search: MagicMock,
        tmp_workspace: Path,
    ) -> None:
        candidates = [
            {
                "title": "Liverpool vs Arsenal Full Match",
                "url": "https://www.youtube.com/watch?v=vid1",
                "duration_seconds": 5700,
                "video_id": "vid1",
            }
        ]
        mock_search.return_value = candidates

        result = find_match("liverpool vs arsenal")

        assert result["type"] == "search_results"
        assert result["candidates"] == candidates
        mock_search.assert_called_once_with("liverpool vs arsenal")

    @patch("pipeline.match_finder._extract_video_id", return_value="short1")
    @patch("pipeline.match_finder.get_video_duration", return_value=300.0)
    def test_url_input_rejects_short_video(
        self,
        _mock_dur: MagicMock,
        _mock_id: MagicMock,
        tmp_workspace: Path,
    ) -> None:
        with (
            patch(
                "pipeline.match_finder._download_video",
                side_effect=lambda _url, w: self._fake_download(_url, w),
            ),
            pytest.raises(MatchFinderError, match="too short"),
        ):
            find_match("https://www.youtube.com/watch?v=short1")


# ── download_and_save ───────────────────────────────────────────────────────


class TestDownloadAndSave:
    @staticmethod
    def _fake_download(_url: str, workspace: Path) -> Path:
        video = workspace / "fake_match.mp4"
        video.write_bytes(b"\x00" * 512)
        return video

    @patch("pipeline.match_finder._extract_video_id", return_value="dl1")
    @patch("pipeline.match_finder.get_video_duration", return_value=5400.0)
    def test_downloads_and_saves_with_fixture_id(
        self,
        _mock_dur: MagicMock,
        _mock_id: MagicMock,
        tmp_workspace: Path,
    ) -> None:
        with patch(
            "pipeline.match_finder._download_video",
            side_effect=lambda _url, w: self._fake_download(_url, w),
        ):
            result = download_and_save(
                "https://www.youtube.com/watch?v=dl1",
                fixture_id=12345,
            )

        assert result["video_id"] == "dl1"
        assert result["fixture_id"] == 12345
        assert result["duration_seconds"] == 5400.0

        meta_path = tmp_workspace / "dl1" / "metadata.json"
        raw = json.loads(meta_path.read_text())
        assert raw["fixture_id"] == 12345

    @patch("pipeline.match_finder._extract_video_id", return_value="dl2")
    @patch("pipeline.match_finder.get_video_duration", return_value=5400.0)
    def test_caching_returns_existing(
        self,
        _mock_dur: MagicMock,
        _mock_id: MagicMock,
        tmp_workspace: Path,
    ) -> None:
        with patch(
            "pipeline.match_finder._download_video",
            side_effect=lambda _url, w: self._fake_download(_url, w),
        ) as mock_dl:
            first = download_and_save("https://www.youtube.com/watch?v=dl2")
            second = download_and_save("https://www.youtube.com/watch?v=dl2")

        assert first == second
        assert mock_dl.call_count == 1

    @patch("pipeline.match_finder._extract_video_id", return_value="dl3")
    @patch("pipeline.match_finder.get_video_duration", return_value=300.0)
    def test_skip_duration_check(
        self,
        _mock_dur: MagicMock,
        _mock_id: MagicMock,
        tmp_workspace: Path,
    ) -> None:
        with patch(
            "pipeline.match_finder._download_video",
            side_effect=lambda _url, w: self._fake_download(_url, w),
        ):
            result = download_and_save(
                "https://www.youtube.com/watch?v=dl3",
                skip_duration_check=True,
            )

        assert result["duration_seconds"] == 300.0

    @patch("pipeline.match_finder._extract_video_id")
    def test_error_on_bad_url(
        self,
        mock_id: MagicMock,
        tmp_workspace: Path,
    ) -> None:
        mock_id.side_effect = MatchFinderError("Could not extract video ID")
        with pytest.raises(MatchFinderError, match="Could not extract"):
            download_and_save("https://bad-url.example.com")


# ── parse_teams_from_video_title / resolve_fixture_for_video ────────────────


class TestParseTeamsFromVideoTitle:
    def test_liverpool_v_real_madrid(self) -> None:
        t = "Liverpool v Real Madrid (0-1) | Champions League Final | Full Match Replay"
        assert parse_teams_from_video_title(t) == ("Liverpool", "Real Madrid")

    def test_psg_vs_bayern(self) -> None:
        t = "PSG vs Bayern Munich (0-1) | UEFA Champions League Final | Full-match Replay"
        assert parse_teams_from_video_title(t) == ("PSG", "Bayern Munich")

    def test_score_in_middle(self) -> None:
        t = "Real Madrid 2-1 Chelsea | FULL MATCH | Chelsea USA Tour 2024"
        assert parse_teams_from_video_title(t) == ("Real Madrid", "Chelsea")


class TestResolveFixtureForVideo:
    @patch("pipeline.match_finder.fetch_headtohead_fixtures")
    def test_returns_unique_when_single_match_in_year(
        self,
        mock_h2h: MagicMock,
    ) -> None:
        mock_h2h.return_value = [
            {
                "fixture_id": 999,
                "home_team": "Liverpool",
                "away_team": "Real Madrid",
                "date": "2024-06-01T20:00:00+00:00",
                "league": "UEFA Champions League",
                "league_id": 2,
                "score": {"home": 0, "away": 1},
            }
        ]
        res = resolve_fixture_for_video(
            "Champions League final 2024",
            "Liverpool v Real Madrid (0-1) | Champions League Final",
        )
        assert res.fixture_id == 999
        assert res.candidates == []
        assert res.teams_parsed is True
        assert res.team_a == "Liverpool"
        assert res.team_b == "Real Madrid"

    @patch("pipeline.match_finder.fetch_headtohead_fixtures")
    def test_returns_candidates_when_ambiguous(self, mock_h2h: MagicMock) -> None:
        mock_h2h.return_value = [
            {
                "fixture_id": 1,
                "home_team": "A",
                "away_team": "B",
                "date": "2024-06-01T20:00:00+00:00",
                "league": "UEFA Champions League",
                "league_id": 2,
                "score": None,
            },
            {
                "fixture_id": 2,
                "home_team": "A",
                "away_team": "B",
                "date": "2024-09-01T20:00:00+00:00",
                "league": "UEFA Champions League",
                "league_id": 2,
                "score": None,
            },
        ]
        res = resolve_fixture_for_video(
            "Champions League final 2024",
            "A v B | Champions League",
        )
        assert res.fixture_id is None
        assert len(res.candidates) == 2

    def test_no_teams_parsed(self) -> None:
        res = resolve_fixture_for_video("some query", "no parseable title here")
        assert res.teams_parsed is False
        assert res.fixture_id is None
        assert res.candidates == []

    @patch("pipeline.match_finder.fetch_headtohead_fixtures")
    def test_api_empty_but_teams_parsed(self, mock_h2h: MagicMock) -> None:
        mock_h2h.return_value = []
        res = resolve_fixture_for_video(
            "final 2024",
            "TeamX v TeamY | Some Cup",
        )
        assert res.teams_parsed is True
        assert res.fixture_id is None
        assert res.candidates == []
