"""Tests for kickoff detection in Stage 2 — transcription module."""

from __future__ import annotations

from pipeline.transcription import detect_kickoffs


def _utt(speaker: str, text: str, start: int, end: int) -> dict:
    """Build an utterance dict with the expected keys."""
    return {"speaker": speaker, "text": text, "start": start, "end": end}


class TestDetectKickoffs:
    def test_first_half_basic(self) -> None:
        utterances = [
            _utt("A", "Welcome to the stadium", 10_000, 15_000),
            _utt("A", "and we're underway", 330_000, 335_000),
        ]
        result = detect_kickoffs(utterances)
        assert result["kickoff_first_half"] == 330.0
        assert result["kickoff_second_half"] is None

    def test_second_half_basic(self) -> None:
        utterances = [
            _utt("A", "and we're underway", 330_000, 335_000),
            _utt("B", "the second half is underway", 3_420_000, 3_425_000),
        ]
        result = detect_kickoffs(utterances)
        assert result["kickoff_second_half"] == 3420.0

    def test_both_halves_detected(self) -> None:
        utterances = [
            _utt("A", "and we are underway at Old Trafford", 300_000, 305_000),
            _utt("A", "half time now", 2_700_000, 2_705_000),
            _utt("B", "the second half is back underway", 3_500_000, 3_505_000),
        ]
        result = detect_kickoffs(utterances)
        assert result["kickoff_first_half"] == 300.0
        assert result["kickoff_second_half"] == 3500.0

    def test_no_matches_returns_none(self) -> None:
        utterances = [
            _utt("A", "Good evening everyone", 1_000, 5_000),
            _utt("B", "What a beautiful day for football", 5_000, 10_000),
        ]
        result = detect_kickoffs(utterances)
        assert result["kickoff_first_half"] is None
        assert result["kickoff_second_half"] is None

    def test_empty_utterances(self) -> None:
        result = detect_kickoffs([])
        assert result["kickoff_first_half"] is None
        assert result["kickoff_second_half"] is None

    def test_multiple_first_half_picks_earliest(self) -> None:
        utterances = [
            _utt("A", "kick off at the stadium", 200_000, 205_000),
            _utt("B", "we're off and running", 350_000, 355_000),
            _utt("A", "the match is underway", 400_000, 405_000),
        ]
        result = detect_kickoffs(utterances)
        assert result["kickoff_first_half"] == 200.0

    def test_second_half_keyword_before_30min_guard_ignored(self) -> None:
        utterances = [
            _utt("A", "we're off", 60_000, 65_000),
            _utt("B", "looking forward to the second half", 300_000, 305_000),
            _utt("B", "back underway for the second period", 3_600_000, 3_605_000),
        ]
        result = detect_kickoffs(utterances)
        assert result["kickoff_first_half"] == 60.0
        assert result["kickoff_second_half"] == 3600.0

    def test_case_insensitivity(self) -> None:
        utterances = [
            _utt("A", "KICK OFF at the Emirates", 500_000, 505_000),
        ]
        result = detect_kickoffs(utterances)
        assert result["kickoff_first_half"] == 500.0

    def test_substring_matching(self) -> None:
        utterances = [
            _utt("A", "the match is underway in Leicester", 600_000, 605_000),
        ]
        result = detect_kickoffs(utterances)
        assert result["kickoff_first_half"] == 600.0

    def test_no_first_half_but_second_half_detected(self) -> None:
        utterances = [
            _utt("A", "Nice day for a match", 100_000, 105_000),
            _utt("B", "second half begins now", 3_300_000, 3_305_000),
        ]
        result = detect_kickoffs(utterances)
        assert result["kickoff_first_half"] is None
        assert result["kickoff_second_half"] == 3300.0
