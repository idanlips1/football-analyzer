"""Tests for Stage 4 — event alignment to video timestamps."""

from __future__ import annotations

import json
from typing import Any

from models.events import AlignedEvent, EventType, MatchEvent
from pipeline.event_aligner import (
    ALIGNMENT_FILENAME,
    align_events,
    estimate_video_timestamp,
    refine_timestamp,
)
from utils.storage import LocalStorage

# ── estimate_video_timestamp ───────────────────────────────────────────────


class TestEstimateVideoTimestamp:
    """Pure arithmetic: kickoff offset + match-minute delta."""

    def test_first_half_goal_minute_21(self) -> None:
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
        ts = estimate_video_timestamp(event, kickoff_first=330.0, kickoff_second=3420.0)
        assert ts == 330.0 + 21 * 60  # 1590

    def test_second_half_goal_minute_70(self) -> None:
        event = MatchEvent(
            minute=70,
            extra_minute=None,
            half="2nd Half",
            event_type=EventType.GOAL,
            team="Manchester City",
            player="Julian Alvarez",
            assist=None,
            score="1 - 1",
            detail="Normal Goal",
        )
        ts = estimate_video_timestamp(event, kickoff_first=330.0, kickoff_second=3420.0)
        assert ts == 3420.0 + (70 - 45) * 60  # 4920

    def test_stoppage_time_90_plus_3(self) -> None:
        event = MatchEvent(
            minute=90,
            extra_minute=3,
            half="2nd Half",
            event_type=EventType.GOAL,
            team="Liverpool",
            player="Darwin Nunez",
            assist="Andrew Robertson",
            score="3 - 1",
            detail="Normal Goal",
        )
        ts = estimate_video_timestamp(event, kickoff_first=330.0, kickoff_second=3420.0)
        assert ts == 3420.0 + 45 * 60 + 3 * 60  # 6300

    def test_first_half_stoppage_45_plus_2(self) -> None:
        event = MatchEvent(
            minute=45,
            extra_minute=2,
            half="1st Half",
            event_type=EventType.FOUL,
            team="X",
            player="Y",
            assist=None,
            score="0 - 0",
            detail="foul",
        )
        ts = estimate_video_timestamp(event, kickoff_first=330.0, kickoff_second=3420.0)
        assert ts == 330.0 + 45 * 60 + 2 * 60

    def test_minute_46_start_of_second_half(self) -> None:
        event = MatchEvent(
            minute=46,
            extra_minute=None,
            half="2nd Half",
            event_type=EventType.GOAL,
            team="X",
            player="Y",
            assist=None,
            score="1 - 0",
            detail="Normal Goal",
        )
        ts = estimate_video_timestamp(event, kickoff_first=330.0, kickoff_second=3420.0)
        assert ts == 3420.0 + 1 * 60  # 3480

    def test_extra_time_minute_100(self) -> None:
        event = MatchEvent(
            minute=100,
            extra_minute=None,
            half="Extra Time",
            event_type=EventType.GOAL,
            team="X",
            player="Y",
            assist=None,
            score="2 - 2",
            detail="Normal Goal",
        )
        ts = estimate_video_timestamp(event, kickoff_first=330.0, kickoff_second=3420.0)
        assert ts == 3420.0 + 45 * 60 + (100 - 90) * 60

    def test_extra_time_second_period_minute_115(self) -> None:
        event = MatchEvent(
            minute=115,
            extra_minute=None,
            half="Extra Time",
            event_type=EventType.GOAL,
            team="X",
            player="Y",
            assist=None,
            score="3 - 3",
            detail="Normal Goal",
        )
        ts = estimate_video_timestamp(event, kickoff_first=330.0, kickoff_second=3420.0)
        assert ts == 3420.0 + 45 * 60 + (115 - 90) * 60

    def test_extra_time_with_extra_minute(self) -> None:
        event = MatchEvent(
            minute=105,
            extra_minute=2,
            half="Extra Time",
            event_type=EventType.GOAL,
            team="X",
            player="Y",
            assist=None,
            score="2 - 2",
            detail="Normal Goal",
        )
        ts = estimate_video_timestamp(event, kickoff_first=330.0, kickoff_second=3420.0)
        assert ts == 3420.0 + 45 * 60 + (105 - 90) * 60 + 2 * 60


# ── refine_timestamp ───────────────────────────────────────────────────────


class TestRefineTimestamp:
    """Audio-based timestamp refinement using utterance proximity."""

    def test_close_utterance_snaps_with_high_confidence(self) -> None:
        utterances = [
            {"speaker": "A", "text": "GOAL!", "start": 1_585_000, "end": 1_590_000},
        ]
        refined_ts, confidence = refine_timestamp(1590.0, utterances)
        assert refined_ts == 1585.0
        assert confidence == 0.9

    def test_no_utterances_in_window_returns_estimate(self) -> None:
        utterances = [
            {"speaker": "A", "text": "Welcome", "start": 100_000, "end": 105_000},
        ]
        refined_ts, confidence = refine_timestamp(1590.0, utterances)
        assert refined_ts == 1590.0
        assert confidence == 0.3

    def test_empty_utterance_list(self) -> None:
        refined_ts, confidence = refine_timestamp(1590.0, [])
        assert refined_ts == 1590.0
        assert confidence == 0.3

    def test_multiple_utterances_picks_closest(self) -> None:
        utterances = [
            {"speaker": "A", "text": "shot!", "start": 1_560_000, "end": 1_565_000},
            {"speaker": "A", "text": "GOAL!", "start": 1_588_000, "end": 1_593_000},
            {"speaker": "B", "text": "what a goal", "start": 1_600_000, "end": 1_605_000},
        ]
        refined_ts, confidence = refine_timestamp(1590.0, utterances)
        assert refined_ts == 1588.0
        assert confidence == 0.9

    def test_utterance_40s_away_gives_medium_confidence(self) -> None:
        utterances = [
            {"speaker": "A", "text": "good pass", "start": 1_550_000, "end": 1_555_000},
        ]
        refined_ts, confidence = refine_timestamp(1590.0, utterances)
        assert refined_ts == 1550.0
        assert confidence == 0.5

    def test_utterance_25s_away_gives_high_mid_confidence(self) -> None:
        utterances = [
            {"speaker": "A", "text": "corner", "start": 1_565_000, "end": 1_570_000},
        ]
        refined_ts, confidence = refine_timestamp(1590.0, utterances)
        assert refined_ts == 1565.0
        assert confidence == 0.7

    def test_prefer_before_picks_latest_preceding_utterance(self) -> None:
        utterances = [
            {"speaker": "A", "text": "build-up", "start": 1_570_000, "end": 1_575_000},
            {"speaker": "A", "text": "shot!", "start": 1_582_000, "end": 1_587_000},
            {"speaker": "A", "text": "GOAL!", "start": 1_595_000, "end": 1_600_000},
        ]
        refined_ts, confidence = refine_timestamp(
            1590.0,
            utterances,
            prefer_before=True,
        )
        assert refined_ts == 1582.0
        assert confidence == 0.9

    def test_prefer_before_falls_back_to_closest_when_none_before(self) -> None:
        utterances = [
            {"speaker": "A", "text": "GOAL!", "start": 1_595_000, "end": 1_600_000},
        ]
        refined_ts, confidence = refine_timestamp(
            1590.0,
            utterances,
            prefer_before=True,
        )
        assert refined_ts == 1595.0
        assert confidence == 0.9

    def test_prefer_before_false_still_picks_closest(self) -> None:
        utterances = [
            {"speaker": "A", "text": "build-up", "start": 1_570_000, "end": 1_575_000},
            {"speaker": "A", "text": "GOAL!", "start": 1_595_000, "end": 1_600_000},
        ]
        refined_ts, confidence = refine_timestamp(
            1590.0,
            utterances,
            prefer_before=False,
        )
        assert refined_ts == 1595.0
        assert confidence == 0.9

    def test_energy_fn_prefers_highest_energy(self) -> None:
        utterances = [
            {"speaker": "A", "text": "soft", "start": 1_588_000, "end": 1_590_000},
            {"speaker": "A", "text": "LOUD!", "start": 1_585_000, "end": 1_590_000},
        ]

        def energy_fn(utt: dict[str, Any]) -> float:
            return 10.0 if "LOUD" in utt["text"] else 1.0

        refined_ts, confidence = refine_timestamp(1590.0, utterances, energy_fn=energy_fn)
        assert refined_ts == 1585.0
        assert confidence == 0.9


# ── align_events (orchestrator) ────────────────────────────────────────────


def _make_transcription_with_utterances(
    kickoff_first: float = 330.0,
    kickoff_second: float = 3420.0,
) -> dict[str, Any]:
    """Build a transcription dict with kickoffs and utterances near known events."""
    return {
        "audio_filename": "audio.wav",
        "total_utterances": 5,
        "commentator_speakers": ["A"],
        "kickoff_first_half": kickoff_first,
        "kickoff_second_half": kickoff_second,
        "utterances": [
            {"speaker": "A", "text": "GOAL!", "start": 1_588_000, "end": 1_593_000},
            {"speaker": "A", "text": "card!", "start": 2_850_000, "end": 2_855_000},
            {"speaker": "A", "text": "equaliser!", "start": 4_918_000, "end": 4_923_000},
            {"speaker": "A", "text": "penalty!", "start": 5_700_000, "end": 5_705_000},
            {"speaker": "A", "text": "seals it!", "start": 6_298_000, "end": 6_303_000},
        ],
    }


class TestAlignEvents:
    def test_full_flow_produces_aligned_events(
        self,
        tmp_storage: LocalStorage,
        sample_match_events: list[MatchEvent],
    ) -> None:
        video_id = "test_vid"
        metadata = {"video_id": video_id}
        transcription = _make_transcription_with_utterances()
        tmp_storage.write_json(video_id, "transcription.json", transcription)

        match_events_data: dict[str, Any] = {
            "video_id": video_id,
            "events": [ev.to_dict() for ev in sample_match_events],
        }

        result = align_events(
            match_events_data,
            metadata,
            tmp_storage,
            kickoff_first=330.0,
            kickoff_second=3420.0,
        )

        assert result["video_id"] == video_id
        assert result["event_count"] > 0
        assert len(result["events"]) == result["event_count"]

        for ev_dict in result["events"]:
            aligned = AlignedEvent.from_dict(ev_dict)
            assert aligned.estimated_video_ts > 0
            assert aligned.refined_video_ts > 0
            assert 0.0 <= aligned.confidence <= 1.0

    def test_substitutions_filtered_out(
        self,
        tmp_storage: LocalStorage,
    ) -> None:
        video_id = "test_vid"
        metadata = {"video_id": video_id}
        transcription = _make_transcription_with_utterances()
        tmp_storage.write_json(video_id, "transcription.json", transcription)

        events = [
            MatchEvent(
                minute=60,
                extra_minute=None,
                half="2nd Half",
                event_type=EventType.SUBSTITUTION,
                team="X",
                player="Sub Player",
                assist=None,
                score="1 - 0",
                detail="Substitution 1",
            ).to_dict(),
            MatchEvent(
                minute=70,
                extra_minute=None,
                half="2nd Half",
                event_type=EventType.GOAL,
                team="X",
                player="Scorer",
                assist=None,
                score="2 - 0",
                detail="Normal Goal",
            ).to_dict(),
        ]

        result = align_events(
            {"video_id": video_id, "events": events},
            metadata,
            tmp_storage,
            kickoff_first=330.0,
            kickoff_second=3420.0,
        )

        assert result["event_count"] == 1
        assert result["events"][0]["event_type"] == EventType.GOAL.value

    def test_caching_returns_cached_file(
        self,
        tmp_storage: LocalStorage,
        sample_match_events: list[MatchEvent],
    ) -> None:
        video_id = "test_vid"
        metadata = {"video_id": video_id}
        transcription = _make_transcription_with_utterances()
        tmp_storage.write_json(video_id, "transcription.json", transcription)

        match_events_data: dict[str, Any] = {
            "video_id": video_id,
            "events": [ev.to_dict() for ev in sample_match_events],
        }

        first = align_events(
            match_events_data,
            metadata,
            tmp_storage,
            kickoff_first=330.0,
            kickoff_second=3420.0,
        )
        second = align_events(
            match_events_data,
            metadata,
            tmp_storage,
            kickoff_first=330.0,
            kickoff_second=3420.0,
        )
        assert first == second

        cache_path = tmp_storage.local_path(video_id, ALIGNMENT_FILENAME)
        assert cache_path.exists()

    def test_explicit_kickoffs_used_for_timestamp_estimation(
        self,
        tmp_storage: LocalStorage,
    ) -> None:
        """Verify that the explicitly passed kickoff values drive timestamp calculation."""
        video_id = "test_vid"
        metadata = {"video_id": video_id}
        # Transcription has NO kickoff fields — kickoffs come from caller args
        transcription: dict[str, Any] = {
            "utterances": [],
        }
        tmp_storage.write_json(video_id, "transcription.json", transcription)

        events = [
            MatchEvent(
                minute=21,
                extra_minute=None,
                half="1st Half",
                event_type=EventType.GOAL,
                team="Liverpool",
                player="Trent Alexander-Arnold",
                assist=None,
                score="1 - 0",
                detail="Normal Goal",
            ).to_dict(),
        ]

        result = align_events(
            {"video_id": video_id, "events": events},
            metadata,
            tmp_storage,
            kickoff_first=330.0,
            kickoff_second=3420.0,
        )

        assert result["event_count"] == 1
        # With kickoff_first=330 and minute=21: 330 + 21*60 = 1590
        assert result["events"][0]["estimated_video_ts"] == 1590.0

    def test_empty_events_produces_empty_result(
        self,
        tmp_storage: LocalStorage,
    ) -> None:
        video_id = "test_vid"
        metadata = {"video_id": video_id}
        transcription = _make_transcription_with_utterances()
        tmp_storage.write_json(video_id, "transcription.json", transcription)

        result = align_events(
            {"video_id": video_id, "events": []},
            metadata,
            tmp_storage,
            kickoff_first=330.0,
            kickoff_second=3420.0,
        )
        assert result["event_count"] == 0
        assert result["events"] == []

    def test_output_file_is_valid_json(
        self,
        tmp_storage: LocalStorage,
        sample_match_events: list[MatchEvent],
    ) -> None:
        video_id = "test_vid"
        metadata = {"video_id": video_id}
        transcription = _make_transcription_with_utterances()
        tmp_storage.write_json(video_id, "transcription.json", transcription)

        match_events_data: dict[str, Any] = {
            "video_id": video_id,
            "events": [ev.to_dict() for ev in sample_match_events],
        }

        align_events(
            match_events_data,
            metadata,
            tmp_storage,
            kickoff_first=330.0,
            kickoff_second=3420.0,
        )

        cache_path = tmp_storage.local_path(video_id, ALIGNMENT_FILENAME)
        loaded = json.loads(cache_path.read_text())
        assert loaded["video_id"] == video_id
        assert isinstance(loaded["events"], list)
