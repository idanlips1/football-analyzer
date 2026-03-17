"""Tests for Stage 4 — EDR scoring, merging, and clip selection (TDD target)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import pipeline.edr as edr_module
from models.events import EDREntry, EventType
from pipeline.edr import EDRError, build_edr, merge_windows, select_clips

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_window(
    start_ms: float,
    end_ms: float,
    score: float,
    event_type: str = "goal",
    **kwargs: Any,
) -> dict[str, Any]:
    return {
        "start_ms": start_ms,
        "end_ms": end_ms,
        "score": score,
        "event_type": event_type,
        "keyword_hits": kwargs.get("keyword_hits", []),
        "energy_peak": kwargs.get("energy_peak", 0.5),
        "video_id": kwargs.get("video_id", "test_video"),
    }


def _make_excitement_data(workspace: Path, windows: list[dict[str, Any]]) -> dict[str, Any]:
    """Write excitement.json in Stage 3 format; return the excitement param for build_edr."""
    vid_id = "test_video"
    vid_workspace = workspace / vid_id
    vid_workspace.mkdir(parents=True, exist_ok=True)
    entries = [
        {
            "timestamp_start": w["start_ms"] / 1000.0,
            "timestamp_end": w["end_ms"] / 1000.0,
            "final_score": w["score"] * 10.0,
            "event_type": w.get("event_type", "goal"),
            "keyword_matches": w.get("keyword_hits", []),
            "commentator_energy": w.get("energy_peak", 0.5),
            "include_in_highlights": True,
            "commentator_text": "",
            "llm_description": "",
            "llm_excitement_score": 0.0,
        }
        for w in windows
    ]
    (vid_workspace / "excitement.json").write_text(json.dumps(entries))
    return {"video_id": vid_id}


# ── TestMergeWindows ──────────────────────────────────────────────────────────


class TestMergeWindows:
    def test_empty_input_returns_empty(self) -> None:
        assert merge_windows([]) == []

    def test_single_window_returns_one_entry(self) -> None:
        w = _make_window(0, 5000, score=0.8)
        result = merge_windows([w])
        assert len(result) == 1
        entry = result[0]
        assert entry.start_seconds == pytest.approx(0.0)
        assert entry.end_seconds == pytest.approx(5.0)
        assert entry.score == pytest.approx(0.8)
        assert entry.event_type == EventType.GOAL
        assert entry.video_id == "test_video"

    def test_non_adjacent_windows_stay_separate(self) -> None:
        w1 = _make_window(0, 5000, score=0.5)
        w2 = _make_window(15000, 20000, score=0.6)
        result = merge_windows([w1, w2])
        assert len(result) == 2

    def test_adjacent_windows_within_gap_are_merged(self) -> None:
        w1 = _make_window(0, 5000, score=0.5)
        w2 = _make_window(6000, 10000, score=0.6)
        result = merge_windows([w1, w2], gap_seconds=2.0)
        assert len(result) == 1
        assert result[0].start_seconds == pytest.approx(0.0)
        assert result[0].end_seconds == pytest.approx(10.0)

    def test_merge_takes_max_score(self) -> None:
        w1 = _make_window(0, 5000, score=0.4)
        w2 = _make_window(6000, 10000, score=0.9)
        result = merge_windows([w1, w2], gap_seconds=2.0)
        assert result[0].score == pytest.approx(0.9)

    def test_merge_takes_max_energy_peak(self) -> None:
        w1 = _make_window(0, 5000, score=0.5, energy_peak=0.3)
        w2 = _make_window(6000, 10000, score=0.5, energy_peak=0.8)
        result = merge_windows([w1, w2], gap_seconds=2.0)
        assert result[0].energy_peak == pytest.approx(0.8)

    def test_merge_unions_keyword_hits(self) -> None:
        w1 = _make_window(0, 5000, score=0.5, keyword_hits=["goal"])
        w2 = _make_window(6000, 10000, score=0.5, keyword_hits=["incredible"])
        result = merge_windows([w1, w2], gap_seconds=2.0)
        assert set(result[0].keyword_hits) == {"goal", "incredible"}

    def test_merge_takes_event_type_of_highest_scoring_window(self) -> None:
        w1 = _make_window(0, 5000, score=0.4, event_type="free_kick")
        w2 = _make_window(4000, 9000, score=0.9, event_type="goal")
        result = merge_windows([w1, w2], gap_seconds=2.0)
        assert len(result) == 1
        assert result[0].event_type == EventType.GOAL

    def test_custom_gap_threshold_respected(self) -> None:
        w1 = _make_window(0, 5000, score=0.5)
        w2 = _make_window(8000, 12000, score=0.6)
        result_default = merge_windows([w1, w2], gap_seconds=2.0)
        result_custom = merge_windows([w1, w2], gap_seconds=5.0)
        assert len(result_default) == 2
        assert len(result_custom) == 1

    def test_overlapping_windows_merged(self) -> None:
        w1 = _make_window(0, 8000, score=0.5)
        w2 = _make_window(5000, 12000, score=0.7)
        result = merge_windows([w1, w2], gap_seconds=2.0)
        assert len(result) == 1
        assert result[0].end_seconds == pytest.approx(12.0)

    def test_output_is_sorted_by_start(self) -> None:
        w1 = _make_window(10000, 15000, score=0.5)
        w2 = _make_window(0, 4000, score=0.8)
        w3 = _make_window(20000, 25000, score=0.6)
        result = merge_windows([w1, w2, w3])
        starts = [e.start_seconds for e in result]
        assert starts == sorted(starts)

    def test_unknown_event_type_becomes_unknown_enum(self) -> None:
        w = _make_window(0, 5000, score=0.5, event_type="xyzzy_totally_invalid")
        result = merge_windows([w])
        assert result[0].event_type == EventType.UNKNOWN

    def test_long_clip_is_capped(self) -> None:
        """Merged clip exceeding max_clip_seconds is trimmed around peak."""
        w1 = _make_window(0, 30000, score=0.4)
        w2 = _make_window(31000, 80000, score=0.9)
        result = merge_windows([w1, w2], gap_seconds=2.0, max_clip_seconds=45.0)
        assert len(result) == 1
        assert result[0].duration <= 45.0 + 0.01

    def test_short_clip_not_affected_by_cap(self) -> None:
        w = _make_window(0, 10000, score=0.8)
        result = merge_windows([w], max_clip_seconds=45.0)
        assert result[0].duration == pytest.approx(10.0)


# ── TestSelectClips ───────────────────────────────────────────────────────────


def _make_entry(
    start_s: float, end_s: float, score: float, event_type: EventType = EventType.GOAL
) -> EDREntry:
    return EDREntry(
        start_seconds=start_s,
        end_seconds=end_s,
        score=score,
        event_type=event_type,
        keyword_hits=[],
        energy_peak=0.5,
        video_id="test_video",
    )


class TestSelectClips:
    def test_empty_input_returns_empty(self) -> None:
        assert select_clips([]) == []

    def test_single_clip_within_budget_is_selected(self) -> None:
        entry = _make_entry(0, 30, 0.8)
        result = select_clips([entry], budget_seconds=60.0)
        assert result == [entry]

    def test_single_clip_exceeding_budget_is_excluded(self) -> None:
        entry = _make_entry(0, 90, 0.9)
        result = select_clips([entry], budget_seconds=60.0)
        assert result == []

    def test_selects_highest_scoring_clips_first(self) -> None:
        low = _make_entry(0, 20, 0.3)
        high = _make_entry(30, 50, 0.9)
        result = select_clips([low, high], budget_seconds=25.0)
        assert high in result
        assert low not in result

    def test_total_duration_does_not_exceed_budget(self) -> None:
        entries = [_make_entry(i * 10, i * 10 + 8, 0.9 - i * 0.1) for i in range(10)]
        budget = 30.0
        result = select_clips(entries, budget_seconds=budget)
        total = sum(e.duration for e in result)
        assert total <= budget

    def test_output_is_chronological(self) -> None:
        e1 = _make_entry(50, 60, 0.9)
        e2 = _make_entry(0, 10, 0.8)
        e3 = _make_entry(25, 35, 0.7)
        result = select_clips([e1, e2, e3], budget_seconds=120.0)
        starts = [e.start_seconds for e in result]
        assert starts == sorted(starts)

    def test_exact_budget_boundary_included(self) -> None:
        entry = _make_entry(0, 60, 0.8)
        result = select_clips([entry], budget_seconds=60.0)
        assert result == [entry]

    def test_custom_budget_respected(self) -> None:
        entries = [_make_entry(i * 10, i * 10 + 9, 0.9) for i in range(5)]
        result = select_clips(entries, budget_seconds=20.0)
        total = sum(e.duration for e in result)
        assert total <= 20.0

    def test_clip_that_would_overflow_budget_is_skipped_not_truncated(self) -> None:
        clip_a = _make_entry(0, 7, 0.9)
        clip_b = _make_entry(10, 16, 0.8)
        result = select_clips([clip_a, clip_b], budget_seconds=10.0)
        assert clip_a in result
        assert clip_b not in result
        for e in result:
            assert e.duration == pytest.approx(e.end_seconds - e.start_seconds)


# ── TestBuildEdr ──────────────────────────────────────────────────────────────


class TestBuildEdr:
    def test_produces_edr_json(self, tmp_workspace: Path) -> None:
        data = _make_excitement_data(tmp_workspace, [_make_window(0, 10000, 0.8)])
        build_edr(data)
        edr_file = tmp_workspace / "test_video" / "edr.json"
        assert edr_file.exists()

    def test_returns_valid_result_dict(self, tmp_workspace: Path) -> None:
        data = _make_excitement_data(tmp_workspace, [_make_window(0, 10000, 0.8)])
        result = build_edr(data)
        for key in (
            "video_id",
            "workspace",
            "clip_count",
            "total_duration_seconds",
            "total_duration_display",
            "clips",
        ):
            assert key in result

    def test_edr_json_is_valid_and_matches_return(self, tmp_workspace: Path) -> None:
        data = _make_excitement_data(tmp_workspace, [_make_window(0, 10000, 0.8)])
        result = build_edr(data)
        edr_file = tmp_workspace / "test_video" / "edr.json"
        on_disk = json.loads(edr_file.read_text())
        assert on_disk == result

    def test_cache_hit_skips_recompute(
        self, tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data = _make_excitement_data(tmp_workspace, [_make_window(0, 10000, 0.8)])
        call_count = [0]
        original = edr_module.merge_windows

        def counting_merge(*args: Any, **kwargs: Any) -> Any:
            call_count[0] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(edr_module, "merge_windows", counting_merge)
        build_edr(data)
        build_edr(data)
        assert call_count[0] == 1

    def test_cache_hit_returns_same_result(self, tmp_workspace: Path) -> None:
        data = _make_excitement_data(tmp_workspace, [_make_window(0, 10000, 0.8)])
        result1 = build_edr(data)
        result2 = build_edr(data)
        assert result1 == result2

    def test_missing_excitement_json_raises_edr_error(self, tmp_workspace: Path) -> None:
        data: dict[str, Any] = {"video_id": "test_video"}
        with pytest.raises(EDRError, match="excitement.json"):
            build_edr(data)

    def test_respects_default_budget(self, tmp_workspace: Path) -> None:
        windows = [_make_window(i * 20000, i * 20000 + 15000, score=0.8) for i in range(50)]
        data = _make_excitement_data(tmp_workspace, windows)
        result = build_edr(data)
        assert result["total_duration_seconds"] <= 600.0

    def test_clip_timestamps_are_hh_mm_ss(self, tmp_workspace: Path) -> None:
        data = _make_excitement_data(tmp_workspace, [_make_window(3661000, 3665000, 0.8)])
        result = build_edr(data)
        clip = result["clips"][0]
        assert clip["start_seconds"] == "01:01:01"
        assert clip["end_seconds"] == "01:01:05"
