"""Tests for config/clip_windows.py."""

from __future__ import annotations

from config.clip_windows import (
    CLIP_WINDOWS,
    EVENT_PRIORITY,
    get_priority,
    get_window,
)
from models.events import EventType


class TestClipWindows:
    def test_goal_window(self) -> None:
        pre, post = get_window(EventType.GOAL)
        assert pre == 15.0
        assert post == 30.0

    def test_penalty_window(self) -> None:
        pre, post = get_window(EventType.PENALTY)
        assert pre == 10.0
        assert post == 25.0

    def test_yellow_card_window(self) -> None:
        pre, post = get_window(EventType.YELLOW_CARD)
        assert pre == 5.0
        assert post == 10.0

    def test_unknown_type_gets_default(self) -> None:
        pre, post = get_window(EventType.OTHER)
        assert pre == 10.0
        assert post == 15.0

    def test_all_highlight_types_have_windows(self) -> None:
        highlight_types = [
            EventType.GOAL,
            EventType.OWN_GOAL,
            EventType.PENALTY,
            EventType.RED_CARD,
            EventType.YELLOW_CARD,
            EventType.NEAR_MISS,
            EventType.SAVE,
            EventType.VAR_REVIEW,
        ]
        for et in highlight_types:
            assert et in CLIP_WINDOWS, f"{et} missing from CLIP_WINDOWS"

    def test_all_windows_positive(self) -> None:
        for et, (pre, post) in CLIP_WINDOWS.items():
            assert pre > 0, f"{et} has non-positive pre-roll"
            assert post > 0, f"{et} has non-positive post-roll"


class TestEventPriority:
    def test_goal_highest_priority(self) -> None:
        assert get_priority(EventType.GOAL) == 0

    def test_yellow_card_lowest_listed(self) -> None:
        assert get_priority(EventType.YELLOW_CARD) == len(EVENT_PRIORITY) - 1

    def test_unlisted_type_lowest(self) -> None:
        assert get_priority(EventType.OTHER) == len(EVENT_PRIORITY)
        assert get_priority(EventType.FOUL) == len(EVENT_PRIORITY)

    def test_goal_beats_penalty(self) -> None:
        assert get_priority(EventType.GOAL) < get_priority(EventType.PENALTY)

    def test_penalty_beats_yellow(self) -> None:
        assert get_priority(EventType.PENALTY) < get_priority(EventType.YELLOW_CARD)

    def test_no_duplicates_in_priority(self) -> None:
        assert len(EVENT_PRIORITY) == len(set(EVENT_PRIORITY))
