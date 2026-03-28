"""FFmpeg/ffprobe helper functions for audio extraction, video cutting, and concatenation."""

import json
import subprocess  # nosec B404
from pathlib import Path

from utils.logger import get_logger

log = get_logger(__name__)


class FFprobeError(Exception):
    """Raised when ffprobe fails or returns unexpected output."""


class FFmpegError(Exception):
    """Raised when an ffmpeg conversion fails."""


def get_video_duration(video_path: Path | str) -> float:
    """Return the duration of a video/URL in seconds using ffprobe.

    Accepts a local path or an HTTP URL (e.g. Azure SAS).
    Raises FFprobeError if ffprobe is not installed or the source cannot be probed.
    """
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        str(video_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)  # nosec B603
    except FileNotFoundError as exc:
        raise FFprobeError(
            "ffprobe not found — install FFmpeg (https://ffmpeg.org/download.html)"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise FFprobeError(f"ffprobe failed for {video_path}: {exc.stderr.strip()}") from exc

    try:
        info = json.loads(result.stdout)
        duration = float(info["format"]["duration"])
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        raise FFprobeError(f"Could not parse duration from ffprobe output: {exc}") from exc

    log.info("Video duration: %.1f seconds (%.1f minutes)", duration, duration / 60)
    return duration


def extract_audio(video_path: Path, output_path: Path) -> Path:
    """Extract the audio track from a video file as mono 16 kHz WAV.

    16 kHz mono is the standard input format for speech-to-text APIs and
    keeps the file size manageable for long matches.

    Returns *output_path* on success.
    Raises FFmpegError if the conversion fails.
    """
    cmd = [
        "ffmpeg",
        "-i",
        str(video_path),
        "-vn",  # drop video
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",  # 16 kHz sample rate
        "-ac",
        "1",  # mono
        "-y",  # overwrite if exists
        str(output_path),
    ]
    log.info("Extracting audio: %s → %s", video_path.name, output_path.name)
    try:
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )  # nosec B603
    except FileNotFoundError as exc:
        raise FFmpegError(
            "ffmpeg not found — install FFmpeg (https://ffmpeg.org/download.html)"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(f"ffmpeg audio extraction failed: {exc.stderr.strip()}") from exc

    if not output_path.exists():
        raise FFmpegError(f"Audio extraction produced no output at {output_path}")

    size_mb = output_path.stat().st_size / (1024 * 1024)
    log.info("Audio extracted: %.1f MB", size_mb)
    return output_path


def cut_clip(
    video_path: Path | str,
    start_seconds: float,
    end_seconds: float,
    output_path: Path,
    *,
    fade_duration: float = 0.0,
) -> Path:
    """Cut a clip from *video_path* between start and end.

    *video_path* may be a local ``Path`` or an HTTP URL (e.g. Azure SAS).
    FFmpeg handles both transparently via byte-range requests for URLs.

    When *fade_duration* > 0, re-encodes with fade-to/from-black on both
    video and audio tracks (libx264 + AAC).  Otherwise uses stream copy
    with fast input-side seeking (``-ss`` before ``-i``).

    Returns *output_path* on success.
    Raises FFmpegError if the cut fails.
    """
    from config.settings import CLIP_AUDIO_BITRATE, CLIP_CRF, CLIP_SCALE

    duration = end_seconds - start_seconds

    if fade_duration > 0:
        fade = min(fade_duration, duration / 2)
        fade_out_start = duration - fade
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start_seconds:.3f}",
            "-i",
            str(video_path),
            "-t",
            f"{duration:.3f}",
            "-vf",
            ",".join(
                filter(
                    None,
                    [
                        f"fade=t=in:st=0:d={fade:.3f},fade=t=out:st={fade_out_start:.3f}:d={fade:.3f}",
                        f"scale={CLIP_SCALE}" if CLIP_SCALE else "",
                    ],
                )
            ),
            "-af",
            f"afade=t=in:st=0:d={fade:.3f},afade=t=out:st={fade_out_start:.3f}:d={fade:.3f}",
            "-c:v",
            "libx264",
            "-crf",
            str(CLIP_CRF),
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-b:a",
            CLIP_AUDIO_BITRATE,
            "-avoid_negative_ts",
            "make_zero",
            str(output_path),
        ]
    else:
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start_seconds:.3f}",
            "-i",
            str(video_path),
            "-t",
            f"{duration:.3f}",
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            str(output_path),
        ]

    log.info("Cutting clip %.1f–%.1fs → %s", start_seconds, end_seconds, output_path.name)
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)  # nosec B603
    except FileNotFoundError as exc:
        raise FFmpegError(
            "ffmpeg not found — install FFmpeg (https://ffmpeg.org/download.html)"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(f"ffmpeg clip cutting failed: {exc.stderr.strip()}") from exc
    if not output_path.exists():
        raise FFmpegError(f"Clip cutting produced no output at {output_path}")
    return output_path


def concat_clips(clip_paths: list[Path], output_path: Path) -> Path:
    """Concatenate *clip_paths* in order using the ffmpeg concat demuxer.

    Writes a temporary file list next to *output_path*, runs ffmpeg, then
    cleans up the list file.
    Returns *output_path* on success.
    Raises FFmpegError if concatenation fails or clip_paths is empty.
    """
    if not clip_paths:
        raise FFmpegError("concat_clips called with an empty clip list")

    list_path = output_path.parent / "_concat_list.txt"
    list_path.write_text("\n".join(f"file '{p.resolve()}'" for p in clip_paths))
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        str(output_path),
    ]
    log.info("Concatenating %d clips → %s", len(clip_paths), output_path.name)
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)  # nosec B603
    except FileNotFoundError as exc:
        raise FFmpegError(
            "ffmpeg not found — install FFmpeg (https://ffmpeg.org/download.html)"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(f"ffmpeg concat failed: {exc.stderr.strip()}") from exc
    finally:
        if list_path.exists():
            list_path.unlink()

    if not output_path.exists():
        raise FFmpegError(f"Concatenation produced no output at {output_path}")
    return output_path


def apply_segment_fades(
    input_path: Path,
    output_path: Path,
    segment_durations: list[float],
    fade_seconds: float,
) -> Path:
    """Re-encode *input_path* with per-segment fade-in/out on the concatenated timeline.

    Each segment gets its own fade-to-black bookends at the boundaries implied
    by *segment_durations*, matching the visual result of applying fades to
    individual clips before concatenation — but in a single encode pass.

    Returns *output_path* on success.
    Raises FFmpegError on failure or if *segment_durations* is empty.
    """
    from config.settings import CLIP_AUDIO_BITRATE, CLIP_CRF, CLIP_SCALE

    if not segment_durations:
        raise FFmpegError("apply_segment_fades called with empty segment list")

    vf_parts: list[str] = []
    af_parts: list[str] = []
    offset = 0.0
    for dur in segment_durations:
        fade = min(fade_seconds, dur / 2)
        fade_out_start = offset + dur - fade
        seg_end = offset + dur
        # enable= restricts each filter to its own time window so a fade-out
        # doesn't permanently zero all subsequent frames in the chain.
        vf_parts.append(
            f"fade=t=in:st={offset:.3f}:d={fade:.3f}"
            f":enable='between(t,{offset:.3f},{offset + fade:.3f})'"
        )
        vf_parts.append(
            f"fade=t=out:st={fade_out_start:.3f}:d={fade:.3f}"
            f":enable='between(t,{fade_out_start:.3f},{seg_end:.3f})'"
        )
        af_parts.append(
            f"afade=t=in:st={offset:.3f}:d={fade:.3f}"
            f":enable='between(t,{offset:.3f},{offset + fade:.3f})'"
        )
        af_parts.append(
            f"afade=t=out:st={fade_out_start:.3f}:d={fade:.3f}"
            f":enable='between(t,{fade_out_start:.3f},{seg_end:.3f})'"
        )
        offset += dur

    if CLIP_SCALE:
        vf_parts.append(f"scale={CLIP_SCALE}")

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        ",".join(vf_parts),
        "-af",
        ",".join(af_parts),
        "-c:v",
        "libx264",
        "-crf",
        str(CLIP_CRF),
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        "-b:a",
        CLIP_AUDIO_BITRATE,
        str(output_path),
    ]
    log.info(
        "Applying per-segment fades (%d segments) → %s",
        len(segment_durations),
        output_path.name,
    )
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)  # nosec B603
    except FileNotFoundError as exc:
        raise FFmpegError(
            "ffmpeg not found — install FFmpeg (https://ffmpeg.org/download.html)"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(f"ffmpeg fade encode failed: {exc.stderr.strip()}") from exc
    if not output_path.exists():
        raise FFmpegError(f"Fade encode produced no output at {output_path}")
    return output_path
