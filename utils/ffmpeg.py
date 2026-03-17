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


def get_video_duration(video_path: Path) -> float:
    """Return the duration of a video file in seconds using ffprobe.

    Raises FFprobeError if ffprobe is not installed or the file cannot be probed.
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
