"""Tests for Stage 4b — event filtering by user-requested types (TDD target)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import pipeline.filtering as filtering_module
from models.events import EDREntry, EventType
from pipeline.filtering import FilteringError, filter_by_type, filter_edr

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_entries(
    specs: list[tuple[float, float, float, EventType]],
) -> list[EDREntry]:
    """(start_s, end_s, score, event_type) → list of EDREntry."""
    return [
        EDREntry(
            start_seconds=start,
            end_seconds=end,
            score=score,
            event_type=et,
            keyword_hits=[],
            energy_peak=0.5,
            video_id="test_video",
        )
        for start, end, score, et in specs
    ]


def _make_edr_data(workspace: Path, clips: list[dict[str, Any]]) -> dict[str, Any]:
    """Write edr.json and return an edr_data dict suitable for filter_edr."""
    vid_id = "test_video"
    vid_workspace = workspace / vid_id
    vid_workspace.mkdir(parents=True, exist_ok=True)
    total_duration = sum(c["end_seconds"] - c["start_seconds"] for c in clips)
    edr_result: dict[str, Any] = {
        "video_id": vid_id,
        "workspace": str(vid_workspace),
        "clip_count": len(clips),
        "total_duration_seconds": total_duration,
        "clips": clips,
    }
    (vid_workspace / "edr.json").write_text(json.dumps(edr_result))
    return edr_result


# ── TestFilterByType ──────────────────────────────────────────────────────────


class TestFilterByType:
    def test_filters_to_single_type(self) -> None:
        entries = _make_entries([(0, 10, 0.8, EventType.GOAL), (10, 20, 0.7, EventType.FREE_KICK)])
        result = filter_by_type(entries, [EventType.GOAL])
        assert len(result) == 1
        assert result[0].event_type == EventType.GOAL

    def test_filters_to_multiple_types(self) -> None:
        entries = _make_entries(
            [
                (0, 10, 0.8, EventType.GOAL),
                (10, 20, 0.7, EventType.FREE_KICK),
                (20, 30, 0.6, EventType.CORNER),
            ]
        )
        result = filter_by_type(entries, [EventType.GOAL, EventType.FREE_KICK])
        assert len(result) == 2
        types = {e.event_type for e in result}
        assert types == {EventType.GOAL, EventType.FREE_KICK}

    def test_empty_event_types_returns_all(self) -> None:
        entries = _make_entries([(0, 10, 0.8, EventType.GOAL), (10, 20, 0.7, EventType.FREE_KICK)])
        result = filter_by_type(entries, [])
        assert result == entries

    def test_no_matching_entries_returns_empty(self) -> None:
        entries = _make_entries([(0, 10, 0.8, EventType.CORNER)])
        result = filter_by_type(entries, [EventType.GOAL])
        assert result == []

    def test_preserves_order(self) -> None:
        entries = _make_entries(
            [
                (0, 10, 0.8, EventType.GOAL),
                (10, 20, 0.7, EventType.GOAL),
                (20, 30, 0.9, EventType.GOAL),
            ]
        )
        result = filter_by_type(entries, [EventType.GOAL])
        assert result == entries

    def test_empty_entry_list_returns_empty(self) -> None:
        assert filter_by_type([], [EventType.GOAL]) == []

    def test_unknown_type_can_be_filtered(self) -> None:
        entries = _make_entries([(0, 10, 0.5, EventType.UNKNOWN)])
        result = filter_by_type(entries, [EventType.UNKNOWN])
        assert len(result) == 1

    def test_does_not_mutate_input_list(self) -> None:
        entries = _make_entries([(0, 10, 0.8, EventType.GOAL), (10, 20, 0.7, EventType.FREE_KICK)])
        original_ids = [id(e) for e in entries]
        filter_by_type(entries, [EventType.GOAL])
        assert [id(e) for e in entries] == original_ids


# ── TestFilterEdr ─────────────────────────────────────────────────────────────


class TestFilterEdr:
    def test_writes_filtered_edr_json(self, tmp_workspace: Path) -> None:
        clips = [e.to_dict() for e in _make_entries([(0, 10, 0.8, EventType.GOAL)])]
        data = _make_edr_data(tmp_workspace, clips)
        filter_edr(data, [EventType.GOAL])
        assert (tmp_workspace / "test_video" / "filtered_edr.json").exists()

    def test_returns_only_matching_clips(self, tmp_workspace: Path) -> None:
        entries = _make_entries([(0, 10, 0.8, EventType.GOAL), (10, 20, 0.7, EventType.FREE_KICK)])
        clips = [e.to_dict() for e in entries]
        data = _make_edr_data(tmp_workspace, clips)
        result = filter_edr(data, [EventType.GOAL])
        assert result["clip_count"] == 1
        assert result["clips"][0]["event_type"] == EventType.GOAL.value

    def test_filtered_edr_json_matches_return(self, tmp_workspace: Path) -> None:
        clips = [e.to_dict() for e in _make_entries([(0, 10, 0.8, EventType.GOAL)])]
        data = _make_edr_data(tmp_workspace, clips)
        result = filter_edr(data, [EventType.GOAL])
        on_disk = json.loads((tmp_workspace / "test_video" / "filtered_edr.json").read_text())
        assert on_disk == result

    def test_cache_hit_skips_recompute(
        self, tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clips = [e.to_dict() for e in _make_entries([(0, 10, 0.8, EventType.GOAL)])]
        data = _make_edr_data(tmp_workspace, clips)
        call_count = [0]
        original = filtering_module.filter_by_type

        def counting_filter(*args: Any, **kwargs: Any) -> Any:
            call_count[0] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(filtering_module, "filter_by_type", counting_filter)
        filter_edr(data, [EventType.GOAL])  # first call: computes
        filter_edr(data, [EventType.GOAL])  # second call: cache hit
        assert call_count[0] == 1

    def test_cache_hit_returns_same_result(self, tmp_workspace: Path) -> None:
        clips = [e.to_dict() for e in _make_entries([(0, 10, 0.8, EventType.GOAL)])]
        data = _make_edr_data(tmp_workspace, clips)
        result1 = filter_edr(data, [EventType.GOAL])
        result2 = filter_edr(data, [EventType.GOAL])
        assert result1 == result2

    def test_empty_event_types_passes_all_clips_through(self, tmp_workspace: Path) -> None:
        entries = _make_entries([(0, 10, 0.8, EventType.GOAL), (10, 20, 0.7, EventType.FREE_KICK)])
        clips = [e.to_dict() for e in entries]
        data = _make_edr_data(tmp_workspace, clips)
        result = filter_edr(data, [])
        assert result["clip_count"] == 2

    def test_no_matching_clips_returns_empty_list(self, tmp_workspace: Path) -> None:
        clips = [e.to_dict() for e in _make_entries([(0, 10, 0.8, EventType.CORNER)])]
        data = _make_edr_data(tmp_workspace, clips)
        result = filter_edr(data, [EventType.GOAL])
        assert result["clip_count"] == 0
        assert result["clips"] == []

    def test_missing_edr_json_raises_filtering_error(self, tmp_workspace: Path) -> None:
        data: dict[str, Any] = {"video_id": "test_video"}
        with pytest.raises(FilteringError, match="edr.json"):
            filter_edr(data, [EventType.GOAL])

    def test_total_duration_recalculated_after_filtering(self, tmp_workspace: Path) -> None:
        entries = _make_entries(
            [
                (0, 10, 0.8, EventType.GOAL),  # 10 s
                (10, 25, 0.7, EventType.FREE_KICK),  # 15 s
                (25, 35, 0.6, EventType.GOAL),  # 10 s
            ]
        )
        clips = [e.to_dict() for e in entries]
        data = _make_edr_data(tmp_workspace, clips)
        result = filter_edr(data, [EventType.GOAL])
        assert result["total_duration_seconds"] == pytest.approx(20.0)
        assert result["clip_count"] == 2
