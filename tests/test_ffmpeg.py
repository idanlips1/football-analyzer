"""Tests for utils/ffmpeg.py — cut_clip, apply_segment_fades, and stream-copy paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from utils.ffmpeg import FFmpegError, apply_segment_fades, cut_clip


def _fake_run_success(
    cmd: list[str],
    **_kwargs: Any,
) -> None:
    """Simulate successful ffmpeg execution and create the output file."""
    # The output path is always the last argument in our ffmpeg commands.
    output = Path(cmd[-1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"fake")


class TestCutClipStreamCopy:
    """Stream-copy path (fade_duration=0)."""

    def test_ss_before_input_for_fast_seek(self, tmp_path: Path) -> None:
        src = tmp_path / "src.mp4"
        src.write_bytes(b"video")
        out = tmp_path / "out.mp4"

        with patch("utils.ffmpeg.subprocess.run", side_effect=_fake_run_success) as mock:
            cut_clip(src, 100.0, 145.0, out, fade_duration=0.0)

        cmd = mock.call_args[0][0]
        ss_idx = cmd.index("-ss")
        i_idx = cmd.index("-i")
        assert ss_idx < i_idx, "-ss must come before -i for fast input-side seeking"

    def test_uses_duration_not_to(self, tmp_path: Path) -> None:
        src = tmp_path / "src.mp4"
        src.write_bytes(b"video")
        out = tmp_path / "out.mp4"

        with patch("utils.ffmpeg.subprocess.run", side_effect=_fake_run_success) as mock:
            cut_clip(src, 100.0, 145.0, out, fade_duration=0.0)

        cmd = mock.call_args[0][0]
        assert "-t" in cmd, "should use -t (duration) not -to"
        assert "-to" not in cmd
        t_val = cmd[cmd.index("-t") + 1]
        assert float(t_val) == pytest.approx(45.0)

    def test_uses_copy_codec(self, tmp_path: Path) -> None:
        src = tmp_path / "src.mp4"
        src.write_bytes(b"video")
        out = tmp_path / "out.mp4"

        with patch("utils.ffmpeg.subprocess.run", side_effect=_fake_run_success) as mock:
            cut_clip(src, 10.0, 20.0, out, fade_duration=0.0)

        cmd = mock.call_args[0][0]
        assert "-c" in cmd
        assert cmd[cmd.index("-c") + 1] == "copy"


class TestCutClipWithFades:
    """Re-encode path with fade-to-black transitions."""

    def test_has_video_and_audio_fade_filters(self, tmp_path: Path) -> None:
        src = tmp_path / "src.mp4"
        src.write_bytes(b"video")
        out = tmp_path / "out.mp4"

        with patch("utils.ffmpeg.subprocess.run", side_effect=_fake_run_success) as mock:
            cut_clip(src, 100.0, 145.0, out, fade_duration=0.5)

        cmd = mock.call_args[0][0]
        assert "-vf" in cmd, "should have video filter"
        assert "-af" in cmd, "should have audio filter"

        vf = cmd[cmd.index("-vf") + 1]
        af = cmd[cmd.index("-af") + 1]
        assert "fade=t=in" in vf
        assert "fade=t=out" in vf
        assert "afade=t=in" in af
        assert "afade=t=out" in af

    def test_fade_out_starts_at_correct_time(self, tmp_path: Path) -> None:
        src = tmp_path / "src.mp4"
        src.write_bytes(b"video")
        out = tmp_path / "out.mp4"

        with patch("utils.ffmpeg.subprocess.run", side_effect=_fake_run_success) as mock:
            cut_clip(src, 100.0, 145.0, out, fade_duration=0.5)

        cmd = mock.call_args[0][0]
        vf = cmd[cmd.index("-vf") + 1]
        # duration=45, fade=0.5 → fade_out_start=44.5
        assert "st=44.500" in vf

    def test_uses_libx264_and_aac(self, tmp_path: Path) -> None:
        src = tmp_path / "src.mp4"
        src.write_bytes(b"video")
        out = tmp_path / "out.mp4"

        with patch("utils.ffmpeg.subprocess.run", side_effect=_fake_run_success) as mock:
            cut_clip(src, 10.0, 20.0, out, fade_duration=0.5)

        cmd = mock.call_args[0][0]
        assert cmd[cmd.index("-c:v") + 1] == "libx264"
        assert cmd[cmd.index("-c:a") + 1] == "aac"

    def test_no_stream_copy_when_fading(self, tmp_path: Path) -> None:
        src = tmp_path / "src.mp4"
        src.write_bytes(b"video")
        out = tmp_path / "out.mp4"

        with patch("utils.ffmpeg.subprocess.run", side_effect=_fake_run_success) as mock:
            cut_clip(src, 10.0, 20.0, out, fade_duration=0.5)

        cmd = mock.call_args[0][0]
        # Should NOT have -c copy (stream copy conflicts with filters)
        copy_indices = [i for i, v in enumerate(cmd) if v == "copy"]
        assert len(copy_indices) == 0

    def test_fade_clamped_for_short_clips(self, tmp_path: Path) -> None:
        src = tmp_path / "src.mp4"
        src.write_bytes(b"video")
        out = tmp_path / "out.mp4"

        # Clip is 2s, fade_duration=5s → should clamp to 1s (duration/2)
        with patch("utils.ffmpeg.subprocess.run", side_effect=_fake_run_success) as mock:
            cut_clip(src, 10.0, 12.0, out, fade_duration=5.0)

        cmd = mock.call_args[0][0]
        vf = cmd[cmd.index("-vf") + 1]
        # Clamped fade = 1.0, fade_out_start = 2.0 - 1.0 = 1.0
        assert "d=1.000" in vf
        assert "st=1.000" in vf


class TestCutClipErrors:
    def test_missing_ffmpeg_raises(self, tmp_path: Path) -> None:
        src = tmp_path / "src.mp4"
        src.write_bytes(b"video")
        out = tmp_path / "out.mp4"

        with (
            patch("utils.ffmpeg.subprocess.run", side_effect=FileNotFoundError),
            pytest.raises(FFmpegError, match="ffmpeg not found"),
        ):
            cut_clip(src, 0.0, 10.0, out)


class TestApplySegmentFades:
    """Single-pass fade encode over a concatenated timeline."""

    def test_filter_chain_for_two_segments(self, tmp_path: Path) -> None:
        src = tmp_path / "concat.mp4"
        src.write_bytes(b"video")
        out = tmp_path / "faded.mp4"

        with patch("utils.ffmpeg.subprocess.run", side_effect=_fake_run_success) as mock:
            apply_segment_fades(src, out, [30.0, 15.0], fade_seconds=0.5)

        cmd = mock.call_args[0][0]
        vf = cmd[cmd.index("-vf") + 1]
        af = cmd[cmd.index("-af") + 1]

        # Segment 0: offset=0, dur=30, fade=0.5 → in st=0, out st=29.5
        assert "fade=t=in:st=0.000:d=0.500:enable='between(t,0.000,0.500)'" in vf
        assert "fade=t=out:st=29.500:d=0.500:enable='between(t,29.500,30.000)'" in vf
        # Segment 1: offset=30, dur=15, fade=0.5 → in st=30, out st=44.5
        assert "fade=t=in:st=30.000:d=0.500:enable='between(t,30.000,30.500)'" in vf
        assert "fade=t=out:st=44.500:d=0.500:enable='between(t,44.500,45.000)'" in vf

        assert "afade=t=in:st=0.000:d=0.500:enable='between(t,0.000,0.500)'" in af
        assert "afade=t=out:st=29.500:d=0.500:enable='between(t,29.500,30.000)'" in af
        assert "afade=t=in:st=30.000:d=0.500:enable='between(t,30.000,30.500)'" in af
        assert "afade=t=out:st=44.500:d=0.500:enable='between(t,44.500,45.000)'" in af

    def test_fade_clamped_for_short_segment(self, tmp_path: Path) -> None:
        src = tmp_path / "concat.mp4"
        src.write_bytes(b"video")
        out = tmp_path / "faded.mp4"

        # 0.6s segment with 1.0s fade → clamp to 0.3s
        with patch("utils.ffmpeg.subprocess.run", side_effect=_fake_run_success) as mock:
            apply_segment_fades(src, out, [0.6], fade_seconds=1.0)

        cmd = mock.call_args[0][0]
        vf = cmd[cmd.index("-vf") + 1]
        assert "d=0.300" in vf

    def test_uses_libx264_and_aac(self, tmp_path: Path) -> None:
        src = tmp_path / "concat.mp4"
        src.write_bytes(b"video")
        out = tmp_path / "faded.mp4"

        with patch("utils.ffmpeg.subprocess.run", side_effect=_fake_run_success) as mock:
            apply_segment_fades(src, out, [10.0], fade_seconds=0.5)

        cmd = mock.call_args[0][0]
        assert cmd[cmd.index("-c:v") + 1] == "libx264"
        assert cmd[cmd.index("-c:a") + 1] == "aac"

    def test_empty_segments_raises(self, tmp_path: Path) -> None:
        src = tmp_path / "concat.mp4"
        src.write_bytes(b"video")
        out = tmp_path / "faded.mp4"

        with pytest.raises(FFmpegError, match="empty segment"):
            apply_segment_fades(src, out, [], fade_seconds=0.5)

    def test_missing_ffmpeg_raises(self, tmp_path: Path) -> None:
        src = tmp_path / "concat.mp4"
        src.write_bytes(b"video")
        out = tmp_path / "faded.mp4"

        with (
            patch("utils.ffmpeg.subprocess.run", side_effect=FileNotFoundError),
            pytest.raises(FFmpegError, match="ffmpeg not found"),
        ):
            apply_segment_fades(src, out, [10.0], fade_seconds=0.5)
