"""Tests for query_interpreter — mocks OpenAI HTTP calls."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from models.events import AlignedEvent, EventType
from models.game import GameState
from models.highlight_query import QueryType
from pipeline.query_interpreter import QueryInterpreterError, interpret_query


def _make_game() -> GameState:
    return GameState(
        video_id="abc",
        home_team="Liverpool",
        away_team="Man City",
        league="Premier League",
        date="2024-10-26",
        fixture_id=1,
        video_filename="match.mp4",
        source="https://www.youtube.com/watch?v=abc",
        duration_seconds=5400.0,
        kickoff_first_half=330.0,
        kickoff_second_half=3420.0,
    )


def _make_aligned_event(
    player: str = "Mohamed Salah", event_type: EventType = EventType.GOAL
) -> AlignedEvent:
    return AlignedEvent(
        event_type=event_type,
        minute=21,
        extra_minute=None,
        half="1st Half",
        player=player,
        team="Liverpool",
        score="1 - 0",
        detail="Normal Goal",
        estimated_video_ts=1590.0,
        refined_video_ts=1590.0,
        confidence=0.9,
    )


def _mock_openai_response(content: str) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


_FAKE_KEY = "sk-test-key"
_KEY_PATCH = "pipeline.query_interpreter.OPENAI_API_KEY"
_CLIENT_PATCH = "pipeline.query_interpreter.OpenAI"


class TestInterpretQuery:
    def test_full_summary_response(self) -> None:
        payload = json.dumps(
            {"query_type": "full_summary", "event_types": None, "player_name": None}
        )
        with patch(_KEY_PATCH, _FAKE_KEY), patch(_CLIENT_PATCH) as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _mock_openai_response(
                payload
            )
            result = interpret_query("show me everything", _make_game(), [_make_aligned_event()])
        assert result.query_type == QueryType.FULL_SUMMARY

    def test_event_filter_response(self) -> None:
        payload = json.dumps(
            {"query_type": "event_filter", "event_types": ["goal", "penalty"], "player_name": None}
        )
        with patch(_KEY_PATCH, _FAKE_KEY), patch(_CLIENT_PATCH) as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _mock_openai_response(
                payload
            )
            result = interpret_query(
                "just goals and penalties", _make_game(), [_make_aligned_event()]
            )
        assert result.query_type == QueryType.EVENT_FILTER
        assert EventType.GOAL in (result.event_types or [])

    def test_player_response(self) -> None:
        payload = json.dumps(
            {"query_type": "player", "event_types": None, "player_name": "Mohamed Salah"}
        )
        with patch(_KEY_PATCH, _FAKE_KEY), patch(_CLIENT_PATCH) as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _mock_openai_response(
                payload
            )
            result = interpret_query("Salah moments", _make_game(), [_make_aligned_event()])
        assert result.query_type == QueryType.PLAYER
        assert result.player_name == "Mohamed Salah"

    def test_malformed_response_falls_back_to_full_summary(self) -> None:
        with patch(_KEY_PATCH, _FAKE_KEY), patch(_CLIENT_PATCH) as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _mock_openai_response(
                "not json at all"
            )
            result = interpret_query("anything", _make_game(), [])
        assert result.query_type == QueryType.FULL_SUMMARY

    def test_api_exception_falls_back_to_full_summary(self) -> None:
        with patch(_KEY_PATCH, _FAKE_KEY), patch(_CLIENT_PATCH) as mock_cls:
            mock_cls.return_value.chat.completions.create.side_effect = Exception("network error")
            result = interpret_query("anything", _make_game(), [])
        assert result.query_type == QueryType.FULL_SUMMARY

    def test_missing_api_key_raises_error(self) -> None:
        with patch(_KEY_PATCH, ""), pytest.raises(QueryInterpreterError, match="OPENAI_API_KEY"):
            interpret_query("anything", _make_game(), [])

    def test_raw_query_preserved(self) -> None:
        payload = json.dumps(
            {"query_type": "full_summary", "event_types": None, "player_name": None}
        )
        with patch(_KEY_PATCH, _FAKE_KEY), patch(_CLIENT_PATCH) as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _mock_openai_response(
                payload
            )
            result = interpret_query("my raw query", _make_game(), [])
        assert result.raw_query == "my raw query"
