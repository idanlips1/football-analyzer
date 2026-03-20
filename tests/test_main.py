"""Tests for main.py helpers."""

from __future__ import annotations

import main


def test_format_duration_hours() -> None:
    assert main._format_duration(3665.0) == "1h01m"


def test_format_duration_minutes_seconds() -> None:
    assert main._format_duration(125.0) == "2m05s"


def test_format_duration_zero() -> None:
    assert main._format_duration(0.0) == "0m00s"
