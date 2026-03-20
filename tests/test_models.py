"""Tests for models/events.py — EventType, MatchEvent, AlignedEvent."""

from __future__ import annotations

from models.events import AlignedEvent, EventType, MatchEvent


class TestEventType:
    def test_own_goal_exists(self) -> None:
        assert EventType("own_goal") == EventType.OWN_GOAL

    def test_substitution_exists(self) -> None:
        assert EventType("substitution") == EventType.SUBSTITUTION

    def test_all_api_football_types_representable(self) -> None:
        api_types = ["goal", "own_goal", "penalty", "yellow_card", "red_card", "substitution"]
        for t in api_types:
            assert EventType(t)


class TestMatchEvent:
    def test_round_trip_basic(self) -> None:
        event = MatchEvent(
            minute=21,
            extra_minute=None,
            half="1st Half",
            event_type=EventType.GOAL,
            team="Liverpool",
            player="Trent Alexander-Arnold",
            assist=None,
            score="1 - 0",
            detail="Normal Goal",
        )
        d = event.to_dict()
        assert d["event_type"] == "goal"
        assert d["assist"] is None
        assert d["extra_minute"] is None

        restored = MatchEvent.from_dict(d)
        assert restored == event

    def test_round_trip_with_assist_and_extra(self) -> None:
        event = MatchEvent(
            minute=90,
            extra_minute=4,
            half="2nd Half",
            event_type=EventType.GOAL,
            team="Liverpool",
            player="Darwin Nunez",
            assist="Andrew Robertson",
            score="3 - 1",
            detail="Normal Goal",
        )
        d = event.to_dict()
        assert d["extra_minute"] == 4
        assert d["assist"] == "Andrew Robertson"

        restored = MatchEvent.from_dict(d)
        assert restored == event

    def test_round_trip_penalty(self) -> None:
        event = MatchEvent(
            minute=83,
            extra_minute=None,
            half="2nd Half",
            event_type=EventType.PENALTY,
            team="Liverpool",
            player="Mohamed Salah",
            assist=None,
            score="2 - 1",
            detail="Penalty",
        )
        assert MatchEvent.from_dict(event.to_dict()) == event

    def test_round_trip_card(self) -> None:
        event = MatchEvent(
            minute=42,
            extra_minute=None,
            half="1st Half",
            event_type=EventType.YELLOW_CARD,
            team="Manchester City",
            player="Ruben Dias",
            assist=None,
            score="1 - 0",
            detail="yellow card",
        )
        assert MatchEvent.from_dict(event.to_dict()) == event


class TestAlignedEvent:
    def test_round_trip(self) -> None:
        event = AlignedEvent(
            event_type=EventType.GOAL,
            minute=21,
            extra_minute=None,
            half="1st Half",
            player="Trent Alexander-Arnold",
            team="Liverpool",
            score="1 - 0",
            detail="Normal Goal",
            estimated_video_ts=1590.0,
            refined_video_ts=1583.2,
            confidence=0.85,
        )
        d = event.to_dict()
        assert d["event_type"] == "goal"
        assert d["estimated_video_ts"] == 1590.0
        assert d["refined_video_ts"] == 1583.2

        restored = AlignedEvent.from_dict(d)
        assert restored == event

    def test_display_time_normal(self) -> None:
        event = AlignedEvent(
            event_type=EventType.GOAL,
            minute=83,
            extra_minute=None,
            half="2nd Half",
            player="Salah",
            team="Liverpool",
            score="2 - 1",
            detail="Penalty",
            estimated_video_ts=5000.0,
            refined_video_ts=4995.0,
            confidence=0.9,
        )
        assert event.display_time == "83'"

    def test_display_time_stoppage(self) -> None:
        event = AlignedEvent(
            event_type=EventType.GOAL,
            minute=90,
            extra_minute=4,
            half="2nd Half",
            player="Nunez",
            team="Liverpool",
            score="3 - 1",
            detail="Normal Goal",
            estimated_video_ts=6000.0,
            refined_video_ts=5995.0,
            confidence=0.7,
        )
        assert event.display_time == "90+4'"

    def test_round_trip_with_extra_minute(self) -> None:
        event = AlignedEvent(
            event_type=EventType.GOAL,
            minute=45,
            extra_minute=2,
            half="1st Half",
            player="Player",
            team="Team",
            score="1 - 0",
            detail="Normal Goal",
            estimated_video_ts=3000.0,
            refined_video_ts=2998.0,
            confidence=0.6,
        )
        assert AlignedEvent.from_dict(event.to_dict()) == event
