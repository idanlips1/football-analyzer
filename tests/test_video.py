"""Tests for Stage 5 — video cutting and highlights assembly."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from pipeline.video import VideoError, build_highlights
from utils.ffmpeg import FFmpegError

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_workspace(tmp_workspace: Path, video_id: str = "test_video") -> Path:
    ws = tmp_workspace / video_id
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _write_metadata(ws: Path, video_filename: str = "video.mp4") -> None:
    metadata = {
        "video_id": ws.name,
        "source": "https://example.com",
        "video_filename": video_filename,
        "duration_seconds": 5400.0,
        "workspace": str(ws),
    }
    (ws / "metadata.json").write_text(json.dumps(metadata))


def _write_source_video(ws: Path, filename: str = "video.mp4") -> Path:
    p = ws / filename
    p.write_bytes(b"fake video content")
    return p


def _make_clip_dict(
    start: str = "00:00:10",
    end: str = "00:00:30",
    event_type: str = "goal",
) -> dict[str, Any]:
    return {
        "start_seconds": start,
        "end_seconds": end,
        "score": 0.8,
        "event_type": event_type,
        "keyword_hits": ["goal"],
        "energy_peak": 0.5,
        "video_id": "test_video",
    }


def _make_filtered_edr(
    ws: Path,
    clips: list[dict[str, Any]] | None = None,
    video_id: str = "test_video",
) -> dict[str, Any]:
    if clips is None:
        clips = [_make_clip_dict()]
    return {
        "video_id": video_id,
        "workspace": str(ws),
        "clip_count": len(clips),
        "total_duration_seconds": 20.0,
        "total_duration_display": "00:00:20",
        "clips": clips,
    }


# ── TestBuildHighlights ───────────────────────────────────────────────────────


class TestBuildHighlights:
    def test_returns_result_dict_with_required_keys(self, tmp_workspace: Path) -> None:
        ws = _make_workspace(tmp_workspace)
        _write_metadata(ws)
        _write_source_video(ws)
        filtered_edr = _make_filtered_edr(ws)

        with (
            patch("pipeline.video.cut_clip"),
            patch("pipeline.video.concat_clips"),
        ):
            (ws / "highlights.mp4").write_bytes(b"fake highlights")
            result = build_highlights(filtered_edr)

        for key in (
            "highlights_path",
            "clip_count",
            "total_duration_seconds",
            "total_duration_display",
        ):
            assert key in result

    def test_cuts_each_clip_once(self, tmp_workspace: Path) -> None:
        ws = _make_workspace(tmp_workspace)
        _write_metadata(ws)
        _write_source_video(ws)
        clips = [_make_clip_dict("00:00:10", "00:00:30"), _make_clip_dict("00:01:00", "00:01:20")]
        filtered_edr = _make_filtered_edr(ws, clips)

        with (
            patch("pipeline.video.cut_clip") as mock_cut,
            patch("pipeline.video.concat_clips"),
        ):
            build_highlights(filtered_edr)

        assert mock_cut.call_count == 2

    def test_concat_called_once_with_all_clip_paths(self, tmp_workspace: Path) -> None:
        ws = _make_workspace(tmp_workspace)
        _write_metadata(ws)
        _write_source_video(ws)
        clips = [_make_clip_dict(), _make_clip_dict("00:01:00", "00:01:20")]
        filtered_edr = _make_filtered_edr(ws, clips)

        with (
            patch("pipeline.video.cut_clip"),
            patch("pipeline.video.concat_clips") as mock_concat,
        ):
            build_highlights(filtered_edr)

        assert mock_concat.call_count == 1
        concat_clip_list = mock_concat.call_args[0][0]
        assert len(concat_clip_list) == 2

    def test_clip_paths_are_inside_clips_subdir(self, tmp_workspace: Path) -> None:
        ws = _make_workspace(tmp_workspace)
        _write_metadata(ws)
        _write_source_video(ws)
        filtered_edr = _make_filtered_edr(ws)

        with (
            patch("pipeline.video.cut_clip") as mock_cut,
            patch("pipeline.video.concat_clips"),
        ):
            build_highlights(filtered_edr)

        clip_path: Path = mock_cut.call_args[0][3]
        assert clip_path.parent.name == "clips"

    def test_cache_hit_skips_ffmpeg(self, tmp_workspace: Path) -> None:
        ws = _make_workspace(tmp_workspace)
        _write_metadata(ws)
        _write_source_video(ws)
        (ws / "highlights.mp4").write_bytes(b"existing highlights")
        filtered_edr = _make_filtered_edr(ws)

        with (
            patch("pipeline.video.cut_clip") as mock_cut,
            patch("pipeline.video.concat_clips") as mock_concat,
        ):
            result = build_highlights(filtered_edr)

        mock_cut.assert_not_called()
        mock_concat.assert_not_called()
        assert "highlights_path" in result

    def test_overwrite_forces_rebuild(self, tmp_workspace: Path) -> None:
        ws = _make_workspace(tmp_workspace)
        _write_metadata(ws)
        _write_source_video(ws)
        (ws / "highlights.mp4").write_bytes(b"old highlights")
        filtered_edr = _make_filtered_edr(ws)

        with (
            patch("pipeline.video.cut_clip") as mock_cut,
            patch("pipeline.video.concat_clips"),
        ):
            (ws / "highlights.mp4").write_bytes(b"new highlights")
            build_highlights(filtered_edr, overwrite=True)

        assert mock_cut.call_count == 1

    def test_empty_clips_raises_video_error(self, tmp_workspace: Path) -> None:
        ws = _make_workspace(tmp_workspace)
        filtered_edr = _make_filtered_edr(ws, clips=[])

        with pytest.raises(VideoError, match="No clips"):
            build_highlights(filtered_edr)

    def test_missing_metadata_raises_video_error(self, tmp_workspace: Path) -> None:
        ws = _make_workspace(tmp_workspace)
        filtered_edr = _make_filtered_edr(ws)

        with pytest.raises(VideoError, match="metadata.json"):
            build_highlights(filtered_edr)

    def test_missing_source_video_raises_video_error(self, tmp_workspace: Path) -> None:
        ws = _make_workspace(tmp_workspace)
        _write_metadata(ws, "video.mp4")
        # Source video file intentionally absent
        filtered_edr = _make_filtered_edr(ws)

        with pytest.raises(VideoError, match="Source video"):
            build_highlights(filtered_edr)

    def test_cut_clip_failure_raises_video_error(self, tmp_workspace: Path) -> None:
        ws = _make_workspace(tmp_workspace)
        _write_metadata(ws)
        _write_source_video(ws)
        filtered_edr = _make_filtered_edr(ws)

        with (
            patch("pipeline.video.cut_clip", side_effect=FFmpegError("cut failed")),
            pytest.raises(VideoError, match="Failed to cut clip"),
        ):
            build_highlights(filtered_edr)

    def test_concat_failure_raises_video_error(self, tmp_workspace: Path) -> None:
        ws = _make_workspace(tmp_workspace)
        _write_metadata(ws)
        _write_source_video(ws)
        filtered_edr = _make_filtered_edr(ws)

        with (
            patch("pipeline.video.cut_clip"),
            patch("pipeline.video.concat_clips", side_effect=FFmpegError("concat failed")),
            pytest.raises(VideoError, match="Failed to concatenate"),
        ):
            build_highlights(filtered_edr)

    def test_result_clip_count_matches_input(self, tmp_workspace: Path) -> None:
        ws = _make_workspace(tmp_workspace)
        _write_metadata(ws)
        _write_source_video(ws)
        clips = [
            _make_clip_dict("00:00:10", "00:00:30"),
            _make_clip_dict("00:01:00", "00:01:20"),
            _make_clip_dict("00:02:00", "00:02:20"),
        ]
        filtered_edr = _make_filtered_edr(ws, clips)

        with (
            patch("pipeline.video.cut_clip"),
            patch("pipeline.video.concat_clips"),
        ):
            (ws / "highlights.mp4").write_bytes(b"fake highlights")
            result = build_highlights(filtered_edr)

        assert result["clip_count"] == 3

    def test_result_highlights_path_is_in_workspace(self, tmp_workspace: Path) -> None:
        ws = _make_workspace(tmp_workspace)
        _write_metadata(ws)
        _write_source_video(ws)
        filtered_edr = _make_filtered_edr(ws)

        with (
            patch("pipeline.video.cut_clip"),
            patch("pipeline.video.concat_clips"),
        ):
            (ws / "highlights.mp4").write_bytes(b"fake highlights")
            result = build_highlights(filtered_edr)

        assert result["highlights_path"].endswith("highlights.mp4")
        assert "test_video" in result["highlights_path"]
