"""Tests for Stage 5b — clip builder (window calculation, merging, budget, assembly)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from models.events import AlignedEvent, EventType
from models.game import GameState
from models.highlight_query import HighlightQuery, QueryType
from pipeline.clip_builder import ClipBuilderError, build_highlights
from utils.storage import LocalStorage

# ── Helpers ───────────────────────────────────────────────────────────────────


def _aligned_event(
    event_type: str = "goal",
    minute: int = 50,
    extra_minute: int | None = None,
    half: str = "2nd Half",
    player: str = "Test Player",
    team: str = "Test FC",
    score: str = "1 - 0",
    detail: str = "Normal Goal",
    estimated_video_ts: float = 1000.0,
    refined_video_ts: float = 1000.0,
    confidence: float = 0.9,
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "minute": minute,
        "extra_minute": extra_minute,
        "half": half,
        "player": player,
        "team": team,
        "score": score,
        "detail": detail,
        "estimated_video_ts": estimated_video_ts,
        "refined_video_ts": refined_video_ts,
        "confidence": confidence,
    }


def _clip(
    clip_start: float = 10.0,
    clip_end: float = 30.0,
    events: list[str] | None = None,
    event_type: str = "goal",
    priority: int = 0,
) -> dict[str, Any]:
    return {
        "clip_start": clip_start,
        "clip_end": clip_end,
        "events": events or ["goal 50' Test Player"],
        "event_type": event_type,
        "priority": priority,
    }


def _write_source_video(ws: Path, filename: str = "video.mp4") -> Path:
    p = ws / filename
    p.write_bytes(b"fake video content")
    return p


def _make_aligned_events_data(
    events: list[dict[str, Any]],
    video_id: str = "test_video",
) -> dict[str, Any]:
    return {
        "video_id": video_id,
        "events": events,
    }


def _make_game(tmp_storage: LocalStorage, video_id: str = "test_video") -> GameState:
    gs = GameState(
        video_id=video_id,
        home_team="A",
        away_team="B",
        league="L",
        date="2024-01-01",
        fixture_id=1,
        video_filename="video.mp4",
        source=f"https://www.youtube.com/watch?v={video_id}",
        duration_seconds=5400.0,
        kickoff_first_half=330.0,
        kickoff_second_half=3420.0,
    )
    return gs


def _make_aligned_events() -> list[AlignedEvent]:
    return [
        AlignedEvent(
            event_type=EventType.GOAL,
            minute=21,
            extra_minute=None,
            half="1st Half",
            player="Test Player",
            team="Test FC",
            score="1-0",
            detail="Normal Goal",
            estimated_video_ts=1590.0,
            refined_video_ts=1590.0,
            confidence=0.9,
        )
    ]


# ── TestCalculateClipWindows ─────────────────────────────────────────────────


class TestCalculateClipWindows:
    def test_goal_at_1000s_gets_25s_pre_and_20s_post(self) -> None:
        from pipeline.clip_builder import calculate_clip_windows

        events = [_aligned_event(refined_video_ts=1000.0)]
        result = calculate_clip_windows(events, video_duration=5400.0)

        assert len(result) == 1
        assert result[0]["clip_start"] == pytest.approx(975.0)
        assert result[0]["clip_end"] == pytest.approx(1020.0)

    def test_event_near_start_clamped_to_zero(self) -> None:
        from pipeline.clip_builder import calculate_clip_windows

        events = [_aligned_event(refined_video_ts=5.0)]
        result = calculate_clip_windows(events, video_duration=5400.0)

        assert result[0]["clip_start"] == 0.0

    def test_event_near_end_clamped_to_video_duration(self) -> None:
        from pipeline.clip_builder import calculate_clip_windows

        duration = 1000.0
        events = [_aligned_event(refined_video_ts=duration - 3.0)]
        result = calculate_clip_windows(events, video_duration=duration)

        assert result[0]["clip_end"] == duration

    def test_multiple_events_sorted_by_clip_start(self) -> None:
        from pipeline.clip_builder import calculate_clip_windows

        events = [
            _aligned_event(refined_video_ts=2000.0, minute=70),
            _aligned_event(refined_video_ts=500.0, minute=20),
            _aligned_event(refined_video_ts=3000.0, minute=85),
        ]
        result = calculate_clip_windows(events, video_duration=5400.0)

        starts = [c["clip_start"] for c in result]
        assert starts == sorted(starts)

    def test_different_event_types_get_different_windows(self) -> None:
        from pipeline.clip_builder import calculate_clip_windows

        events = [
            _aligned_event(
                event_type="goal",
                refined_video_ts=1000.0,
                minute=50,
            ),
            _aligned_event(
                event_type="yellow_card",
                estimated_video_ts=2000.0,
                refined_video_ts=2000.0,
                minute=70,
                player="Other Player",
            ),
        ]
        result = calculate_clip_windows(events, video_duration=5400.0)

        goal_clip = result[0]
        card_clip = result[1]
        goal_duration = goal_clip["clip_end"] - goal_clip["clip_start"]
        card_duration = card_clip["clip_end"] - card_clip["clip_start"]
        assert goal_duration > card_duration

    def test_event_summary_format_normal(self) -> None:
        from pipeline.clip_builder import calculate_clip_windows

        events = [
            _aligned_event(
                event_type="goal",
                minute=21,
                player="Trent Alexander-Arnold",
            ),
        ]
        result = calculate_clip_windows(events, video_duration=5400.0)
        assert "goal 21' Trent Alexander-Arnold" in result[0]["events"]

    def test_event_summary_format_extra_time(self) -> None:
        from pipeline.clip_builder import calculate_clip_windows

        events = [
            _aligned_event(
                event_type="goal",
                minute=90,
                extra_minute=4,
                player="Darwin Nunez",
            ),
        ]
        result = calculate_clip_windows(events, video_duration=5400.0)
        assert "goal 90+4' Darwin Nunez" in result[0]["events"]

    def test_pre_roll_uses_earlier_of_estimated_and_refined(self) -> None:
        from pipeline.clip_builder import calculate_clip_windows

        events = [
            _aligned_event(
                estimated_video_ts=1010.0,
                refined_video_ts=1000.0,
            ),
        ]
        result = calculate_clip_windows(events, video_duration=5400.0)
        # pre_roll anchored from min(1010, 1000) = 1000 → clip_start = 975
        assert result[0]["clip_start"] == pytest.approx(975.0)

    def test_pre_roll_when_refined_after_estimated(self) -> None:
        from pipeline.clip_builder import calculate_clip_windows

        events = [
            _aligned_event(
                estimated_video_ts=1000.0,
                refined_video_ts=1010.0,
            ),
        ]
        result = calculate_clip_windows(events, video_duration=5400.0)
        # pre_roll anchored from min(1000, 1010) = 1000 → clip_start = 975
        assert result[0]["clip_start"] == pytest.approx(975.0)
        # post_roll still from refined (20s for goal)
        assert result[0]["clip_end"] == pytest.approx(1030.0)

    def test_clip_has_priority_field(self) -> None:
        from pipeline.clip_builder import calculate_clip_windows

        events = [_aligned_event(event_type="yellow_card")]
        result = calculate_clip_windows(events, video_duration=5400.0)
        assert "priority" in result[0]
        assert isinstance(result[0]["priority"], int)


# ── TestMergeClips ───────────────────────────────────────────────────────────


class TestMergeClips:
    def test_close_clips_merged(self) -> None:
        from pipeline.clip_builder import merge_clips

        clips = [
            _clip(clip_start=100.0, clip_end=130.0),
            _clip(clip_start=133.0, clip_end=160.0),
        ]
        result = merge_clips(clips, gap_seconds=5.0)
        assert len(result) == 1
        assert result[0]["clip_start"] == 100.0
        assert result[0]["clip_end"] == 160.0

    def test_distant_clips_stay_separate(self) -> None:
        from pipeline.clip_builder import merge_clips

        clips = [
            _clip(clip_start=100.0, clip_end=130.0),
            _clip(clip_start=160.0, clip_end=190.0),
        ]
        result = merge_clips(clips, gap_seconds=5.0)
        assert len(result) == 2

    def test_three_overlapping_clips_merge_to_one(self) -> None:
        from pipeline.clip_builder import merge_clips

        clips = [
            _clip(clip_start=100.0, clip_end=130.0, events=["goal 50' A"]),
            _clip(clip_start=125.0, clip_end=155.0, events=["save 52' B"]),
            _clip(clip_start=150.0, clip_end=180.0, events=["near_miss 54' C"]),
        ]
        result = merge_clips(clips, gap_seconds=5.0)
        assert len(result) == 1
        assert result[0]["clip_start"] == 100.0
        assert result[0]["clip_end"] == 180.0

    def test_events_combined_when_merging(self) -> None:
        from pipeline.clip_builder import merge_clips

        clips = [
            _clip(clip_start=100.0, clip_end=130.0, events=["goal 50' A"]),
            _clip(clip_start=128.0, clip_end=160.0, events=["save 52' B"]),
        ]
        result = merge_clips(clips, gap_seconds=5.0)
        assert "goal 50' A" in result[0]["events"]
        assert "save 52' B" in result[0]["events"]

    def test_priority_preserved_best_of_merged(self) -> None:
        from pipeline.clip_builder import merge_clips

        clips = [
            _clip(clip_start=100.0, clip_end=130.0, priority=5),
            _clip(clip_start=128.0, clip_end=160.0, priority=0),
        ]
        result = merge_clips(clips, gap_seconds=5.0)
        assert result[0]["priority"] == 0


# ── TestEnforceBudget ────────────────────────────────────────────────────────


class TestEnforceBudget:
    def test_under_budget_no_change(self) -> None:
        from pipeline.clip_builder import enforce_budget

        clips = [
            _clip(clip_start=100.0, clip_end=130.0),
            _clip(clip_start=200.0, clip_end=230.0),
        ]
        result = enforce_budget(clips, budget_seconds=600.0)
        assert len(result) == 2

    def test_over_budget_drops_lowest_priority(self) -> None:
        from pipeline.clip_builder import enforce_budget

        clips = [
            _clip(clip_start=100.0, clip_end=145.0, priority=0, event_type="goal"),
            _clip(clip_start=200.0, clip_end=245.0, priority=0, event_type="goal"),
            _clip(
                clip_start=300.0,
                clip_end=315.0,
                priority=8,
                event_type="yellow_card",
            ),
        ]
        # 45 + 45 + 15 = 105s total; budget = 95s → should drop yellow card, keep both goals
        result = enforce_budget(clips, budget_seconds=95.0)
        assert len(result) == 2
        for c in result:
            assert c["event_type"] == "goal"

    def test_single_clip_exceeding_budget_still_included(self) -> None:
        from pipeline.clip_builder import enforce_budget

        clips = [_clip(clip_start=0.0, clip_end=700.0)]
        result = enforce_budget(clips, budget_seconds=600.0)
        assert len(result) == 1

    def test_result_sorted_chronologically(self) -> None:
        from pipeline.clip_builder import enforce_budget

        clips = [
            _clip(clip_start=500.0, clip_end=530.0, priority=0),
            _clip(clip_start=100.0, clip_end=130.0, priority=0),
            _clip(clip_start=300.0, clip_end=330.0, priority=0),
        ]
        result = enforce_budget(clips, budget_seconds=600.0)
        starts = [c["clip_start"] for c in result]
        assert starts == sorted(starts)


# ── TestQuerySlug ────────────────────────────────────────────────────────────


class TestQuerySlug:
    def test_normal_query(self) -> None:
        from pipeline.clip_builder import _query_slug

        q = HighlightQuery(query_type=QueryType.FULL_SUMMARY, raw_query="show me everything")
        assert _query_slug(q) == "show_me_everything"

    def test_special_chars_stripped(self) -> None:
        from pipeline.clip_builder import _query_slug

        q = HighlightQuery(query_type=QueryType.FULL_SUMMARY, raw_query="???!!!")
        assert _query_slug(q) == QueryType.FULL_SUMMARY.value

    def test_empty_raw_query_falls_back_to_type(self) -> None:
        from pipeline.clip_builder import _query_slug

        q = HighlightQuery(query_type=QueryType.EVENT_FILTER, raw_query="")
        assert _query_slug(q) == QueryType.EVENT_FILTER.value

    def test_long_query_truncated_to_40(self) -> None:
        from pipeline.clip_builder import _query_slug

        q = HighlightQuery(
            query_type=QueryType.FULL_SUMMARY,
            raw_query="a" * 50,
        )
        assert len(_query_slug(q)) == 40


# ── TestBuildHighlights ──────────────────────────────────────────────────────


class TestBuildHighlights:
    def _mock_cut(
        self,
        _video_path: Path,
        _start: float,
        _end: float,
        output_path: Path,
        **_kwargs: Any,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"clip")
        return output_path

    def _mock_ffmpeg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Patch cut/concat/fades/probe so no real FFmpeg is needed."""
        monkeypatch.setattr("pipeline.clip_builder.cut_clip", lambda *a, **kw: None)
        monkeypatch.setattr(
            "pipeline.clip_builder.concat_clips",
            lambda paths, out: out.write_bytes(b""),
        )
        monkeypatch.setattr(
            "pipeline.clip_builder.apply_segment_fades",
            lambda inp, out, durations, fade: out.write_bytes(b""),
        )
        monkeypatch.setattr("pipeline.clip_builder.get_video_duration", lambda _: 120.0)

    def test_build_creates_highlights(
        self, tmp_storage: LocalStorage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        video_id = "test_video"
        game = _make_game(tmp_storage, video_id)
        ws = tmp_storage.workspace_path(video_id)
        (ws / "video.mp4").write_bytes(b"fake")

        self._mock_ffmpeg(monkeypatch)

        q = HighlightQuery(query_type=QueryType.FULL_SUMMARY, raw_query="summary")
        result = build_highlights(
            _make_aligned_events(),
            game,
            q,
            tmp_storage,
            confirm_overwrite_fn=lambda _: True,
        )
        assert "highlights_path" in result
        assert result["clip_count"] == 1

    def test_slug_in_output_filename(
        self, tmp_storage: LocalStorage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        video_id = "test_video"
        game = _make_game(tmp_storage, video_id)
        ws = tmp_storage.workspace_path(video_id)
        (ws / "video.mp4").write_bytes(b"fake")

        self._mock_ffmpeg(monkeypatch)

        q = HighlightQuery(query_type=QueryType.FULL_SUMMARY, raw_query="summary")
        result = build_highlights(
            _make_aligned_events(),
            game,
            q,
            tmp_storage,
            confirm_overwrite_fn=lambda _: True,
        )
        assert "highlights_summary" in result["highlights_path"]

    def test_slug_collision_skip(self, tmp_storage: LocalStorage) -> None:
        video_id = "test_video"
        game = _make_game(tmp_storage, video_id)
        ws = tmp_storage.workspace_path(video_id)
        (ws / "highlights_summary.mp4").write_bytes(b"existing")

        q = HighlightQuery(query_type=QueryType.FULL_SUMMARY, raw_query="summary")
        result = build_highlights(
            _make_aligned_events(),
            game,
            q,
            tmp_storage,
            confirm_overwrite_fn=lambda _: False,
        )
        assert "highlights_path" in result

    def test_actual_clip_durations_used_for_fades(
        self, tmp_storage: LocalStorage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fade timing must use probed clip durations, not manifest estimates.

        Stream-copy cutting with input-side -ss seeking can produce clips that
        are longer than (clip_end - clip_start) by up to one keyframe interval.
        Passing manifest estimates to apply_segment_fades would place fade-outs
        too early; probed actual durations fix the alignment.
        """
        from config.settings import FADE_DURATION_SECONDS

        if FADE_DURATION_SECONDS <= 0:
            pytest.skip("Fades disabled in config")

        video_id = "test_video"
        game = _make_game(tmp_storage, video_id)
        ws = tmp_storage.workspace_path(video_id)
        (ws / "video.mp4").write_bytes(b"fake")

        probed_clip_duration = 47.5  # simulates keyframe-alignment overshoot

        captured: dict[str, Any] = {}

        def mock_get_duration(path: Path) -> float:
            if "clip_" in path.name:
                return probed_clip_duration
            return 120.0  # final highlights probe

        def mock_apply_fades(inp: Path, out: Path, durations: list[float], fade: float) -> None:
            captured["durations"] = list(durations)
            out.write_bytes(b"")

        monkeypatch.setattr("pipeline.clip_builder.cut_clip", lambda *a, **kw: None)
        monkeypatch.setattr(
            "pipeline.clip_builder.concat_clips",
            lambda paths, out: out.write_bytes(b""),
        )
        monkeypatch.setattr("pipeline.clip_builder.apply_segment_fades", mock_apply_fades)
        monkeypatch.setattr("pipeline.clip_builder.get_video_duration", mock_get_duration)

        q = HighlightQuery(query_type=QueryType.FULL_SUMMARY, raw_query="summary")
        build_highlights(
            _make_aligned_events(),
            game,
            q,
            tmp_storage,
            confirm_overwrite_fn=lambda _: True,
        )

        assert "durations" in captured, "apply_segment_fades was not called"
        assert captured["durations"] == [probed_clip_duration], (
            "apply_segment_fades received manifest estimates instead of probed durations"
        )

    def test_empty_events_raises_error(self, tmp_storage: LocalStorage) -> None:
        game = _make_game(tmp_storage)
        q = HighlightQuery(query_type=QueryType.FULL_SUMMARY, raw_query="summary")
        with pytest.raises(ClipBuilderError, match="[Nn]o.*event"):
            build_highlights(
                [],
                game,
                q,
                tmp_storage,
                confirm_overwrite_fn=lambda _: True,
            )
