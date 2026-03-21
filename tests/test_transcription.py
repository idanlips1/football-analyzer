"""Tests for Stage 2 — transcription and commentator identification."""

from __future__ import annotations

import json
from unittest.mock import patch

from pipeline.transcription import (
    _find_halftime_gap,
    _find_halftime_keyword,
    _has_kickoff_action,
    _is_narrative_mention,
    _scan_bridge_words,
    detect_kickoffs,
    identify_commentators,
    transcribe,
)
from utils.storage import LocalStorage

# ── identify_commentators ──────────────────────────────────────────────────


class TestIdentifyCommentators:
    def test_single_dominant_speaker(self) -> None:
        utterances = [
            {"speaker": "A", "start": 0, "end": 6000},
            {"speaker": "B", "start": 6000, "end": 7000},
            {"speaker": "C", "start": 7000, "end": 7500},
        ]
        # A has 6s, B has 1s (16%), C has 0.5s (8%) — only A qualifies at 30%
        assert identify_commentators(utterances) == ["A"]

    def test_two_commentators(self) -> None:
        utterances = [
            {"speaker": "A", "start": 0, "end": 5000},
            {"speaker": "B", "start": 5000, "end": 8000},
            {"speaker": "A", "start": 8000, "end": 13000},
            {"speaker": "B", "start": 13000, "end": 15000},
        ]
        # A = 10s, B = 5s (50% of A) — both qualify at default 30%
        assert identify_commentators(utterances) == ["A", "B"]

    def test_three_speakers_two_commentators(self) -> None:
        utterances = [
            {"speaker": "A", "start": 0, "end": 10000},
            {"speaker": "B", "start": 10000, "end": 16000},
            {"speaker": "C", "start": 16000, "end": 17000},
        ]
        # A = 10s, B = 6s (60%), C = 1s (10%) — A and B qualify
        assert identify_commentators(utterances) == ["A", "B"]

    def test_empty_utterances(self) -> None:
        assert identify_commentators([]) == []

    def test_single_speaker(self) -> None:
        utterances = [
            {"speaker": "X", "start": 0, "end": 5000},
            {"speaker": "X", "start": 6000, "end": 9000},
        ]
        assert identify_commentators(utterances) == ["X"]

    def test_custom_ratio_stricter(self) -> None:
        utterances = [
            {"speaker": "A", "start": 0, "end": 10000},
            {"speaker": "B", "start": 10000, "end": 14000},
        ]
        # B = 4s = 40% of A's 10s. With ratio=0.5, B doesn't qualify.
        assert identify_commentators(utterances, time_ratio=0.5) == ["A"]

    def test_custom_ratio_looser(self) -> None:
        utterances = [
            {"speaker": "A", "start": 0, "end": 10000},
            {"speaker": "B", "start": 10000, "end": 12000},
            {"speaker": "C", "start": 12000, "end": 13000},
        ]
        # A=10s, B=2s (20%), C=1s (10%). With ratio=0.1, all qualify.
        assert identify_commentators(utterances, time_ratio=0.1) == ["A", "B", "C"]

    def test_result_is_sorted(self) -> None:
        utterances = [
            {"speaker": "C", "start": 0, "end": 5000},
            {"speaker": "A", "start": 5000, "end": 10000},
        ]
        assert identify_commentators(utterances) == ["A", "C"]


# ── transcribe (integration with mocked AssemblyAI) ────────────────────────


class TestTranscribe:
    @staticmethod
    def _make_metadata(storage: LocalStorage, video_id: str) -> dict:
        ws = storage.workspace_path(video_id)
        (ws / "match.mp4").write_bytes(b"\x00" * 512)
        return {
            "video_id": video_id,
            "video_filename": "match.mp4",
        }

    @staticmethod
    def _fake_utterances() -> list[dict]:
        return [
            {"speaker": "A", "text": "What a goal!", "start": 0, "end": 5000},
            {"speaker": "B", "text": "Incredible.", "start": 5000, "end": 8000},
            {"speaker": "A", "text": "He scores!", "start": 8000, "end": 12000},
        ]

    def test_full_flow_saves_transcription(self, tmp_storage: LocalStorage) -> None:
        metadata = self._make_metadata(tmp_storage, "test1")
        fake_utts = self._fake_utterances()

        with (
            patch("pipeline.transcription.extract_audio"),
            patch("pipeline.transcription._call_assemblyai", return_value=fake_utts),
        ):
            result = transcribe(metadata, tmp_storage)

        assert result["total_utterances"] == 3
        assert result["commentator_speakers"] == ["A", "B"]
        assert tmp_storage.local_path("test1", "transcription.json").exists()

    def test_cache_hit_skips_processing(self, tmp_storage: LocalStorage) -> None:
        metadata = self._make_metadata(tmp_storage, "test2")
        fake_utts = self._fake_utterances()

        with (
            patch("pipeline.transcription.extract_audio"),
            patch("pipeline.transcription._call_assemblyai", return_value=fake_utts) as mock_api,
        ):
            first = transcribe(metadata, tmp_storage)
            second = transcribe(metadata, tmp_storage)

        assert first == second
        assert mock_api.call_count == 1

    def test_skips_audio_extraction_if_file_exists(self, tmp_storage: LocalStorage) -> None:
        metadata = self._make_metadata(tmp_storage, "test3")
        tmp_storage.local_path("test3", "audio.wav").write_bytes(b"\x00" * 256)

        with (
            patch("pipeline.transcription.extract_audio") as mock_extract,
            patch(
                "pipeline.transcription._call_assemblyai",
                return_value=self._fake_utterances(),
            ),
        ):
            transcribe(metadata, tmp_storage)

        mock_extract.assert_not_called()

    def test_transcription_json_is_valid(self, tmp_storage: LocalStorage) -> None:
        metadata = self._make_metadata(tmp_storage, "test4")

        with (
            patch("pipeline.transcription.extract_audio"),
            patch(
                "pipeline.transcription._call_assemblyai",
                return_value=self._fake_utterances(),
            ),
        ):
            result = transcribe(metadata, tmp_storage)

        raw = json.loads(tmp_storage.local_path("test4", "transcription.json").read_text())
        assert raw["utterances"] == result["utterances"]
        assert raw["commentator_speakers"] == ["A", "B"]


# ── _find_halftime_gap ────────────────────────────────────────────────────


def _utt(start_s: float, end_s: float, text: str = "filler") -> dict:
    """Helper: build an utterance dict from seconds."""
    return {"speaker": "A", "text": text, "start": int(start_s * 1000), "end": int(end_s * 1000)}


class TestFindHalftimeGap:
    def test_clear_halftime_gap(self) -> None:
        """A 10-minute silence at ~45 min is detected as halftime."""
        utts = [
            _utt(1700, 1710),  # commentary ending before halftime
            _utt(1790, 1800),  # last utterance before halftime (30:00)
            # --- 10 minute gap (halftime) ---
            _utt(2400, 2410),  # commentary resumes (40:00)
            _utt(2500, 2510),
        ]
        result = _find_halftime_gap(utts)
        assert result is not None
        assert abs(result - 2400.0) < 1.0

    def test_no_gap_returns_none(self) -> None:
        """Continuous commentary → no halftime gap detected."""
        utts = [_utt(1800 + i * 10, 1808 + i * 10) for i in range(200)]
        result = _find_halftime_gap(utts)
        assert result is None

    def test_short_gap_ignored(self) -> None:
        """Gaps shorter than _MIN_GAP_SECONDS (15s) are ignored."""
        utts = [
            _utt(1800, 1810),
            # 10s gap — below 15s threshold
            _utt(1820, 1830),
            # 12s gap — also below threshold
            _utt(1842, 1852),
        ]
        result = _find_halftime_gap(utts)
        assert result is None

    def test_picks_longest_gap(self) -> None:
        """When multiple large gaps exist, the longest wins."""
        utts = [
            _utt(1800, 1810),
            # 60s gap
            _utt(1870, 1880),
            # 300s gap (the real halftime)
            _utt(2180, 2190),
            _utt(2200, 2210),
        ]
        result = _find_halftime_gap(utts)
        assert result is not None
        assert abs(result - 2180.0) < 1.0

    def test_empty_utterances(self) -> None:
        assert _find_halftime_gap([]) is None


# ── _is_narrative_mention / _has_kickoff_action ───────────────────────────


class TestNarrativeFiltering:
    def test_narrative_mention_detected(self) -> None:
        assert _is_narrative_mention("he came on for the second half against bayern")

    def test_non_narrative_passes(self) -> None:
        assert not _is_narrative_mention("the second half is underway")

    def test_kickoff_action_detected(self) -> None:
        assert _has_kickoff_action("second half underway now")

    def test_no_action_phrase(self) -> None:
        assert not _has_kickoff_action("the second half was tough")


# ── detect_kickoffs (integration) ─────────────────────────────────────────


class TestDetectKickoffs:
    def test_gap_based_detection(self) -> None:
        """Gap detection finds second half even without keywords."""
        utts = [
            _utt(5, 20, "and we kick off at wembley"),  # first half
            _utt(100, 110),
            # Dense first-half commentary in the gap search window
            *[_utt(1800 + i * 15, 1810 + i * 15) for i in range(50)],
            _utt(2550, 2560),  # last before halftime
            # --- 340s halftime gap ---
            _utt(2900, 2910, "just some filler talk here"),
            _utt(3000, 3010),
        ]
        result = detect_kickoffs(utts)
        assert result["kickoff_first_half"] is not None
        assert abs(result["kickoff_first_half"] - 5.0) < 1.0
        assert result["kickoff_second_half"] is not None
        assert abs(result["kickoff_second_half"] - 2900.0) < 1.0

    def test_narrative_mention_rejected(self) -> None:
        """'second half against Bayern' at 29 min is rejected."""
        utts = [
            _utt(960, 26240, "he only came on for the second half against Bayern Munich"),
            _utt(1700, 1710),
            # --- halftime gap ---
            _utt(2876, 2900, "the second half is underway"),
            _utt(3000, 3010),
        ]
        result = detect_kickoffs(utts)
        # Should pick ~2876s, not 960s
        assert result["kickoff_second_half"] is not None
        assert result["kickoff_second_half"] > 2800

    def test_absolute_guard_without_first_half(self) -> None:
        """Even without first-half detection, early 'second half' is rejected."""
        utts = [
            _utt(500, 510, "second half was brilliant last week"),
            _utt(1700, 1710),
            # --- halftime gap ---
            _utt(2900, 2910, "the second half kicks off"),
            _utt(3000, 3010),
        ]
        result = detect_kickoffs(utts)
        assert result["kickoff_first_half"] is None
        # Must not pick the 500s narrative mention
        assert result["kickoff_second_half"] is not None
        assert result["kickoff_second_half"] > 2800

    def test_keyword_and_gap_agree(self) -> None:
        """When gap and keyword are close, keyword is preferred (more precise)."""
        utts = [
            _utt(5, 15, "here we go"),
            _utt(1700, 1710),
            # --- halftime gap ---
            _utt(2870, 2880, "just chatter"),  # gap resumes here
            _utt(2900, 2910, "the second half is underway"),  # keyword here
        ]
        result = detect_kickoffs(utts)
        assert result["kickoff_second_half"] is not None
        # Should pick keyword (2900) not gap (2870) since they're within 3 min
        assert abs(result["kickoff_second_half"] - 2900.0) < 1.0

    def test_no_utterances(self) -> None:
        result = detect_kickoffs([])
        assert result["kickoff_first_half"] is None
        assert result["kickoff_second_half"] is None

    def test_halftime_keyword_with_action_phrase(self) -> None:
        """Halftime keyword found, then an action phrase after."""
        utts = [
            _utt(5, 15, "here we go"),
            _utt(2700, 2720, "into added time just two minutes"),
            _utt(2800, 2810, "some discussion"),
            _utt(2900, 2910, "second half is underway"),
            _utt(3000, 3010),
        ]
        result = detect_kickoffs(utts)
        assert result["kickoff_second_half"] is not None
        assert abs(result["kickoff_second_half"] - 2900.0) < 1.0

    def test_halftime_keyword_with_bridge_utterance(self) -> None:
        """Halftime keyword found, then a long bridge utterance spans the break."""
        utts = [
            _utt(5, 15, "here we go"),
            _utt(2700, 2720, "into added time just two minutes"),
            # Bridge: 180s utterance spanning halftime analysis
            _utt(2730, 2910, "talking about the first half tactics and analysis " * 5),
            _utt(2920, 2940, "city pressing high now"),
            _utt(3300, 3310, "start of this second half"),
        ]
        result = detect_kickoffs(utts)
        assert result["kickoff_second_half"] is not None
        # Should pick bridge end (2910) not the late keyword (3300)
        assert abs(result["kickoff_second_half"] - 2910.0) < 1.0


# ── _find_halftime_keyword ────────────────────────────────────────────────


class TestFindHalftimeKeyword:
    def test_added_time_with_action(self) -> None:
        utts = [
            _utt(2700, 2710, "into added time"),
            _utt(2900, 2910, "second half underway"),
        ]
        result = _find_halftime_keyword(utts)
        assert result is not None
        assert abs(result - 2900.0) < 1.0

    def test_bridge_utterance(self) -> None:
        """Long utterance after halftime marker → use its end."""
        utts = [
            _utt(2700, 2710, "stoppage time now"),
            _utt(2720, 2900, "long studio analysis " * 10),  # 180s bridge
        ]
        result = _find_halftime_keyword(utts)
        assert result is not None
        assert abs(result - 2900.0) < 1.0

    def test_no_halftime_keyword(self) -> None:
        utts = [
            _utt(2700, 2710, "what a first half"),
            _utt(2900, 2910, "back again"),
        ]
        result = _find_halftime_keyword(utts)
        assert result is None

    def test_short_utterance_not_treated_as_bridge(self) -> None:
        """A short utterance after the marker is not a bridge."""
        utts = [
            _utt(2700, 2710, "into the break now"),
            _utt(2720, 2740, "short comment"),  # only 20s, not a bridge
        ]
        result = _find_halftime_keyword(utts)
        # No action phrase, no bridge → None
        assert result is None

    def test_bridge_with_word_level_scanning(self) -> None:
        """Bridge utterance with word timestamps finds transition point."""
        bridge_utt = {
            "speaker": "A",
            "text": "analysis of the first half plays it down the wing",
            "start": 2720000,
            "end": 2900000,
            "words": [
                {"text": "analysis", "start": 2720000, "end": 2725000},
                {"text": "of", "start": 2726000, "end": 2727000},
                {"text": "the", "start": 2728000, "end": 2729000},
                {"text": "first", "start": 2730000, "end": 2732000},
                {"text": "half", "start": 2733000, "end": 2735000},
                # midpoint = 2810000 — words below are in second half
                {"text": "plays", "start": 2850000, "end": 2852000},
                {"text": "it", "start": 2853000, "end": 2854000},
                {"text": "down", "start": 2855000, "end": 2857000},
                {"text": "the", "start": 2858000, "end": 2859000},
                {"text": "wing", "start": 2860000, "end": 2862000},
            ],
        }
        utts = [
            _utt(2700, 2710, "added time"),
            bridge_utt,
        ]
        result = _find_halftime_keyword(utts)
        assert result is not None
        # Should find "plays it down the wing" window starting at 2850s
        assert abs(result - 2850.0) < 1.0


# ── _scan_bridge_words ────────────────────────────────────────────────────


def _word(text: str, start_s: float, end_s: float) -> dict:
    """Helper: build a word dict from seconds."""
    return {"text": text, "start": int(start_s * 1000), "end": int(end_s * 1000)}


class TestScanBridgeWords:
    def test_finds_live_signal_in_second_half(self) -> None:
        utt = {
            "start": 2700000,
            "end": 2900000,
            "words": [
                _word("looking", 2700, 2702),
                _word("at", 2703, 2704),
                _word("the", 2705, 2706),
                _word("stats", 2707, 2710),
                # midpoint = 2800 — words below are in second half
                _word("plays", 2850, 2852),
                _word("it", 2853, 2854),
                _word("down", 2855, 2857),
                _word("the", 2858, 2859),
                _word("wing", 2860, 2862),
            ],
        }
        result = _scan_bridge_words(utt)
        assert result is not None
        # "plays it down the wing" window starts at "plays" = 2850s
        assert abs(result - 2850.0) < 1.0

    def test_no_signal_returns_none(self) -> None:
        utt = {
            "start": 2700000,
            "end": 2900000,
            "words": [
                _word("talking", 2700, 2702),
                _word("about", 2810, 2812),
                _word("tactics", 2820, 2822),
            ],
        }
        result = _scan_bridge_words(utt)
        assert result is None

    def test_no_words_returns_none(self) -> None:
        utt = {"start": 2700000, "end": 2900000, "words": []}
        assert _scan_bridge_words(utt) is None

    def test_missing_words_key_returns_none(self) -> None:
        utt = {"start": 2700000, "end": 2900000}
        assert _scan_bridge_words(utt) is None

    def test_ignores_first_half_of_utterance(self) -> None:
        """Signals in the first half of the utterance are ignored."""
        utt = {
            "start": 2700000,
            "end": 2900000,
            "words": [
                _word("shoots", 2710, 2712),  # before midpoint — ignored
                _word("he", 2720, 2722),
                _word("ordinary", 2810, 2812),  # after midpoint, no signal
                _word("stuff", 2820, 2822),
            ],
        }
        result = _scan_bridge_words(utt)
        assert result is None

    def test_second_half_keyword_detected(self) -> None:
        utt = {
            "start": 2700000,
            "end": 2900000,
            "words": [
                _word("well", 2700, 2702),
                _word("then", 2710, 2712),
                # midpoint = 2800
                _word("second", 2850, 2852),
                _word("half", 2853, 2855),
                _word("underway", 2856, 2860),
            ],
        }
        result = _scan_bridge_words(utt)
        assert result is not None
        assert abs(result - 2850.0) < 1.0
