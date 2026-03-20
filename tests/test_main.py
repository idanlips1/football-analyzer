"""Tests for main.py helpers."""

from __future__ import annotations

import pytest

import main


def test_parse_two_teams_basic() -> None:
    assert main._parse_two_teams("Liverpool, Arsenal") == ("Liverpool", "Arsenal")


def test_parse_two_teams_extra_whitespace() -> None:
    assert main._parse_two_teams("  Chelsea ,  Man City  ") == ("Chelsea", "Man City")


def test_parse_two_teams_too_few() -> None:
    assert main._parse_two_teams("OnlyOne") is None


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (3665.0, "1h01m"),
        (125.0, "2m05s"),
        (0.0, "0m00s"),
    ],
)
def test_format_duration(seconds: float, expected: str) -> None:
    assert main._format_duration(seconds) == expected
