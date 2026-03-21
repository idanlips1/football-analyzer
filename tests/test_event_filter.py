"""Tests for event_filter — pure function, no mocks needed."""

from __future__ import annotations

from models.events import AlignedEvent, EventType
from models.highlight_query import HighlightQuery, QueryType
from pipeline.event_filter import filter_events


def _ae(
    event_type: EventType = EventType.GOAL,
    player: str = "Test Player",
    minute: int = 50,
) -> AlignedEvent:
    return AlignedEvent(
        event_type=event_type,
        minute=minute,
        extra_minute=None,
        half="2nd Half",
        player=player,
        team="Test FC",
        score="1 - 0",
        detail="Normal Goal",
        estimated_video_ts=1000.0,
        refined_video_ts=1000.0,
        confidence=0.9,
    )


EVENTS = [
    _ae(EventType.GOAL, "Mohamed Salah", 21),
    _ae(EventType.YELLOW_CARD, "Ruben Dias", 42),
    _ae(EventType.GOAL, "Julian Alvarez", 70),
    _ae(EventType.PENALTY, "Mohamed Salah", 83),
    _ae(EventType.RED_CARD, "John Doe", 88),
]


class TestFullSummary:
    def test_returns_all_events(self) -> None:
        q = HighlightQuery(query_type=QueryType.FULL_SUMMARY)
        assert filter_events(EVENTS, q) == EVENTS


class TestEventFilter:
    def test_goals_only(self) -> None:
        q = HighlightQuery(query_type=QueryType.EVENT_FILTER, event_types=[EventType.GOAL])
        result = filter_events(EVENTS, q)
        assert len(result) == 2
        assert all(e.event_type == EventType.GOAL for e in result)

    def test_goals_and_penalties(self) -> None:
        q = HighlightQuery(
            query_type=QueryType.EVENT_FILTER,
            event_types=[EventType.GOAL, EventType.PENALTY],
        )
        result = filter_events(EVENTS, q)
        assert len(result) == 3

    def test_none_event_types_falls_back_to_all(self) -> None:
        q = HighlightQuery(query_type=QueryType.EVENT_FILTER, event_types=None)
        result = filter_events(EVENTS, q)
        assert result == EVENTS

    def test_no_matches_falls_back_to_all(self) -> None:
        q = HighlightQuery(query_type=QueryType.EVENT_FILTER, event_types=[EventType.CORNER])
        result = filter_events(EVENTS, q)
        assert result == EVENTS


class TestPlayerFilter:
    def test_exact_name_match(self) -> None:
        q = HighlightQuery(query_type=QueryType.PLAYER, player_name="Mohamed Salah")
        result = filter_events(EVENTS, q)
        assert len(result) == 2
        assert all(e.player == "Mohamed Salah" for e in result)

    def test_fuzzy_name_match(self) -> None:
        # "Mohamad Salah" is close enough to "Mohamed Salah" to score >= 0.6 in difflib
        q = HighlightQuery(query_type=QueryType.PLAYER, player_name="Mohamad Salah")
        result = filter_events(EVENTS, q)
        assert len(result) == 2

    def test_substring_only_match(self) -> None:
        # "Salah" scores < 0.6 vs "Mohamed Salah" in difflib, falls through to substring
        q = HighlightQuery(query_type=QueryType.PLAYER, player_name="Salah")
        result = filter_events(EVENTS, q)
        assert len(result) == 2

    def test_substring_fallback(self) -> None:
        q = HighlightQuery(query_type=QueryType.PLAYER, player_name="Alvarez")
        result = filter_events(EVENTS, q)
        assert len(result) == 1
        assert result[0].player == "Julian Alvarez"

    def test_no_player_name_falls_back_to_all(self) -> None:
        q = HighlightQuery(query_type=QueryType.PLAYER, player_name=None)
        result = filter_events(EVENTS, q)
        assert result == EVENTS

    def test_unknown_player_falls_back_to_all(self) -> None:
        q = HighlightQuery(query_type=QueryType.PLAYER, player_name="Nonexistent Player XYZ")
        result = filter_events(EVENTS, q)
        assert result == EVENTS
