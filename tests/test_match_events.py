"""Tests for match events fetching from API-Football (api-sports.io)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from models.events import EventType
from pipeline.match_events import (
    MatchEventsError,
    _map_event_type,
    _parse_events,
    fetch_match_events,
)

# ── Realistic API response fixtures ────────────────────────────────────────


def _make_api_event(
    elapsed: int,
    *,
    extra: int | None = None,
    team_name: str = "FC Tulsa",
    player_name: str = "H. St.Clair",
    assist_name: str | None = "Bruno Lapa",
    event_type: str = "Goal",
    detail: str = "Normal Goal",
) -> dict[str, Any]:
    return {
        "time": {"elapsed": elapsed, "extra": extra},
        "team": {"id": 4022, "name": team_name},
        "player": {"id": 31371, "name": player_name},
        "assist": {"id": 547294 if assist_name else None, "name": assist_name},
        "type": event_type,
        "detail": detail,
        "comments": None,
    }


SAMPLE_RAW_EVENTS: list[dict[str, Any]] = [
    _make_api_event(19, player_name="H. St.Clair", detail="Normal Goal"),
    _make_api_event(
        35,
        player_name="J. Doe",
        assist_name=None,
        event_type="Card",
        detail="Yellow Card",
    ),
    _make_api_event(
        55,
        player_name="B. Smith",
        team_name="Opponent FC",
        event_type="subst",
        detail="Substitution 1",
    ),
    _make_api_event(
        72,
        player_name="C. Jones",
        event_type="Var",
        detail="Goal cancelled",
    ),
    _make_api_event(
        80,
        player_name="D. Lee",
        detail="Penalty",
    ),
    _make_api_event(
        88,
        player_name="E. Kim",
        event_type="Card",
        detail="Red Card",
    ),
    _make_api_event(
        90,
        extra=3,
        player_name="F. Garcia",
        detail="Own Goal",
    ),
]


# ── _map_event_type ────────────────────────────────────────────────────────


class TestMapEventType:
    def test_normal_goal(self) -> None:
        assert _map_event_type("Goal", "Normal Goal") == EventType.GOAL

    def test_own_goal(self) -> None:
        assert _map_event_type("Goal", "Own Goal") == EventType.OWN_GOAL

    def test_penalty_goal(self) -> None:
        assert _map_event_type("Goal", "Penalty") == EventType.PENALTY

    def test_missed_penalty(self) -> None:
        assert _map_event_type("Goal", "Missed Penalty") == EventType.MISSED_PENALTY

    def test_yellow_card(self) -> None:
        assert _map_event_type("Card", "Yellow Card") == EventType.YELLOW_CARD

    def test_red_card(self) -> None:
        assert _map_event_type("Card", "Red Card") == EventType.RED_CARD

    def test_second_yellow(self) -> None:
        assert _map_event_type("Card", "Second Yellow card") == EventType.RED_CARD

    def test_substitution(self) -> None:
        assert _map_event_type("subst", "Substitution 1") == EventType.SUBSTITUTION

    def test_var_review(self) -> None:
        assert _map_event_type("Var", "Goal cancelled") == EventType.VAR_REVIEW

    def test_unknown_falls_to_other(self) -> None:
        assert _map_event_type("SomethingNew", "Whatever") == EventType.OTHER

    def test_goal_confirmed_var(self) -> None:
        assert _map_event_type("Var", "Goal confirmed") == EventType.VAR_REVIEW

    def test_penalty_confirmed_var(self) -> None:
        assert _map_event_type("Var", "Penalty confirmed") == EventType.VAR_REVIEW


# ── _parse_events ──────────────────────────────────────────────────────────


class TestParseEvents:
    def test_parses_goal(self) -> None:
        events = _parse_events([_make_api_event(19)])
        assert len(events) == 1
        e = events[0]
        assert e.minute == 19
        assert e.extra_minute is None
        assert e.half == "1st Half"
        assert e.event_type == EventType.GOAL
        assert e.team == "FC Tulsa"
        assert e.player == "H. St.Clair"
        assert e.assist == "Bruno Lapa"
        assert e.detail == "Normal Goal"

    def test_half_assignment_first_half(self) -> None:
        events = _parse_events([_make_api_event(1), _make_api_event(45)])
        assert events[0].half == "1st Half"
        assert events[1].half == "1st Half"

    def test_half_assignment_second_half(self) -> None:
        events = _parse_events([_make_api_event(46), _make_api_event(90)])
        assert events[0].half == "2nd Half"
        assert events[1].half == "2nd Half"

    def test_half_assignment_extra_time(self) -> None:
        events = _parse_events([_make_api_event(121)])
        assert events[0].half == "Extra Time"

    def test_extra_minute_preserved(self) -> None:
        events = _parse_events([_make_api_event(90, extra=3)])
        assert events[0].minute == 90
        assert events[0].extra_minute == 3

    def test_null_assist_becomes_none(self) -> None:
        raw = _make_api_event(10, assist_name=None)
        events = _parse_events([raw])
        assert events[0].assist is None

    def test_empty_list(self) -> None:
        assert _parse_events([]) == []

    def test_multiple_events_preserve_order(self) -> None:
        events = _parse_events(SAMPLE_RAW_EVENTS)
        assert len(events) == len(SAMPLE_RAW_EVENTS)
        assert events[0].minute == 19
        assert events[-1].minute == 90

    def test_score_is_empty_string(self) -> None:
        """Events endpoint doesn't include running score."""
        events = _parse_events([_make_api_event(10)])
        assert events[0].score == ""

    def test_substitution_type(self) -> None:
        raw = _make_api_event(60, event_type="subst", detail="Substitution 1")
        events = _parse_events([raw])
        assert events[0].event_type == EventType.SUBSTITUTION


# ── fetch_match_events (orchestrator) ──────────────────────────────────────


def _build_metadata(
    tmp_workspace: Path,
    video_id: str = "test_vid",
    fixture_id: int = 12345,
) -> dict[str, Any]:
    ws = tmp_workspace / video_id
    ws.mkdir(parents=True, exist_ok=True)
    return {"video_id": video_id, "fixture_id": fixture_id}


class TestFetchMatchEvents:
    @patch("pipeline.match_events._fetch_events", return_value=SAMPLE_RAW_EVENTS)
    def test_returns_correct_structure(
        self,
        _mock_fetch: MagicMock,
        tmp_workspace: Path,
    ) -> None:
        metadata = _build_metadata(tmp_workspace)
        result = fetch_match_events(metadata)

        assert result["video_id"] == "test_vid"
        assert result["fixture_id"] == 12345
        assert result["event_count"] == len(SAMPLE_RAW_EVENTS)
        assert len(result["events"]) == len(SAMPLE_RAW_EVENTS)

    @patch("pipeline.match_events._fetch_events", return_value=SAMPLE_RAW_EVENTS)
    def test_events_are_dicts(
        self,
        _mock_fetch: MagicMock,
        tmp_workspace: Path,
    ) -> None:
        metadata = _build_metadata(tmp_workspace)
        result = fetch_match_events(metadata)
        for ev in result["events"]:
            assert isinstance(ev, dict)
            assert "minute" in ev
            assert "event_type" in ev

    @patch("pipeline.match_events._fetch_events", return_value=SAMPLE_RAW_EVENTS)
    def test_caching_writes_file(
        self,
        _mock_fetch: MagicMock,
        tmp_workspace: Path,
    ) -> None:
        metadata = _build_metadata(tmp_workspace)
        fetch_match_events(metadata)
        cache_path = tmp_workspace / "test_vid" / "match_events.json"
        assert cache_path.exists()
        cached = json.loads(cache_path.read_text())
        assert cached["event_count"] == len(SAMPLE_RAW_EVENTS)

    @patch("pipeline.match_events._fetch_events", return_value=SAMPLE_RAW_EVENTS)
    def test_cache_hit_skips_api_call(
        self,
        mock_fetch: MagicMock,
        tmp_workspace: Path,
    ) -> None:
        metadata = _build_metadata(tmp_workspace)
        first = fetch_match_events(metadata)
        second = fetch_match_events(metadata)
        assert first == second
        assert mock_fetch.call_count == 1

    @patch("pipeline.match_events._fetch_events", return_value=[])
    def test_empty_events(
        self,
        _mock_fetch: MagicMock,
        tmp_workspace: Path,
    ) -> None:
        metadata = _build_metadata(tmp_workspace)
        result = fetch_match_events(metadata)
        assert result["event_count"] == 0
        assert result["events"] == []

    def test_missing_api_key_raises(
        self,
        tmp_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("pipeline.match_events.API_FOOTBALL_KEY", "")
        metadata = _build_metadata(tmp_workspace)
        with pytest.raises(MatchEventsError, match="API_FOOTBALL_KEY"):
            fetch_match_events(metadata)

    def test_missing_fixture_id_raises(
        self,
        tmp_workspace: Path,
    ) -> None:
        metadata: dict[str, Any] = {"video_id": "test_vid"}
        (tmp_workspace / "test_vid").mkdir(parents=True, exist_ok=True)
        with pytest.raises(MatchEventsError, match="fixture_id"):
            fetch_match_events(metadata)
