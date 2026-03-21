"""Tests for Stage 5b — clip builder (window calculation, merging, budget, assembly)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

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


def _make_workspace(tmp_workspace: Path, video_id: str = "test_video") -> Path:
    ws = tmp_workspace / video_id
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _write_metadata(
    ws: Path,
    video_filename: str = "video.mp4",
    duration: float = 5400.0,
) -> None:
    metadata = {
        "video_id": ws.name,
        "source": "https://example.com",
        "video_filename": video_filename,
        "duration_seconds": duration,
        "workspace": str(ws),
    }
    (ws / "metadata.json").write_text(json.dumps(metadata))


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
        # 45 + 45 + 15 = 105s total; budget = 95s → should drop yellow card
        result = enforce_budget(clips, budget_seconds=95.0)
        assert len(result) == 2
        for c in result:
            assert c["event_type"] != "yellow_card"

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

    def _mock_concat(self, _clip_paths: list[Path], output_path: Path) -> Path:
        output_path.write_bytes(b"highlights")
        return output_path

    def test_full_flow(self, tmp_workspace: Path) -> None:
        from pipeline.clip_builder import build_highlights

        ws = _make_workspace(tmp_workspace)
        _write_metadata(ws, duration=5400.0)
        _write_source_video(ws)

        aligned = _make_aligned_events_data(
            [
                _aligned_event(
                    event_type="goal",
                    minute=21,
                    refined_video_ts=1000.0,
                    player="Trent Alexander-Arnold",
                ),
                _aligned_event(
                    event_type="penalty",
                    minute=83,
                    refined_video_ts=4500.0,
                    player="Mohamed Salah",
                ),
            ]
        )
        metadata = {
            "video_id": "test_video",
            "video_filename": "video.mp4",
            "duration_seconds": 5400.0,
        }

        with (
            patch("pipeline.clip_builder.cut_clip", side_effect=self._mock_cut),
            patch("pipeline.clip_builder.concat_clips", side_effect=self._mock_concat),
        ):
            result = build_highlights(aligned, metadata)

        assert "highlights_path" in result
        assert result["clip_count"] >= 1
        assert result["total_duration_seconds"] > 0
        assert "total_duration_display" in result
        assert "clips" in result

    def test_manifest_written(self, tmp_workspace: Path) -> None:
        from pipeline.clip_builder import MANIFEST_FILENAME, build_highlights

        ws = _make_workspace(tmp_workspace)
        _write_metadata(ws, duration=5400.0)
        _write_source_video(ws)

        aligned = _make_aligned_events_data(
            [
                _aligned_event(refined_video_ts=1000.0),
            ]
        )
        metadata = {
            "video_id": "test_video",
            "video_filename": "video.mp4",
            "duration_seconds": 5400.0,
        }

        with (
            patch("pipeline.clip_builder.cut_clip", side_effect=self._mock_cut),
            patch("pipeline.clip_builder.concat_clips", side_effect=self._mock_concat),
        ):
            build_highlights(aligned, metadata)

        manifest_path = ws / MANIFEST_FILENAME
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        assert isinstance(manifest, list)
        assert len(manifest) >= 1

    def test_correct_ffmpeg_calls(self, tmp_workspace: Path) -> None:
        from pipeline.clip_builder import build_highlights

        ws = _make_workspace(tmp_workspace)
        _write_metadata(ws, duration=5400.0)
        _write_source_video(ws)

        aligned = _make_aligned_events_data(
            [
                _aligned_event(
                    estimated_video_ts=1000.0,
                    refined_video_ts=1000.0,
                    minute=50,
                ),
                _aligned_event(
                    estimated_video_ts=3000.0,
                    refined_video_ts=3000.0,
                    minute=80,
                    event_type="penalty",
                    player="Other Player",
                ),
            ]
        )
        metadata = {
            "video_id": "test_video",
            "video_filename": "video.mp4",
            "duration_seconds": 5400.0,
        }

        with (
            patch(
                "pipeline.clip_builder.cut_clip",
                side_effect=self._mock_cut,
            ) as mock_cut,
            patch(
                "pipeline.clip_builder.concat_clips",
                side_effect=self._mock_concat,
            ) as mock_concat,
        ):
            build_highlights(aligned, metadata)

        assert mock_cut.call_count == 2
        assert mock_concat.call_count == 1
        concat_clip_list = mock_concat.call_args[0][0]
        assert len(concat_clip_list) == 2

    def test_cache_hit_skips_rebuild(self, tmp_workspace: Path) -> None:
        from pipeline.clip_builder import HIGHLIGHTS_FILENAME, build_highlights

        ws = _make_workspace(tmp_workspace)
        _write_metadata(ws, duration=5400.0)
        _write_source_video(ws)
        (ws / HIGHLIGHTS_FILENAME).write_bytes(b"existing highlights")

        aligned = _make_aligned_events_data(
            [
                _aligned_event(refined_video_ts=1000.0),
            ]
        )
        metadata = {
            "video_id": "test_video",
            "video_filename": "video.mp4",
            "duration_seconds": 5400.0,
        }

        with (
            patch("pipeline.clip_builder.cut_clip") as mock_cut,
            patch("pipeline.clip_builder.concat_clips") as mock_concat,
        ):
            result = build_highlights(aligned, metadata, overwrite=False)

        mock_cut.assert_not_called()
        mock_concat.assert_not_called()
        assert "highlights_path" in result

    def test_empty_events_raises_error(self, tmp_workspace: Path) -> None:
        from pipeline.clip_builder import ClipBuilderError, build_highlights

        aligned = _make_aligned_events_data([])
        metadata = {
            "video_id": "test_video",
            "video_filename": "video.mp4",
            "duration_seconds": 5400.0,
        }

        with pytest.raises(ClipBuilderError, match="[Nn]o.*event"):
            build_highlights(aligned, metadata)
