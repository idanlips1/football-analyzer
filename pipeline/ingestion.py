"""Stage 1 — Video ingestion: download a YouTube video and save it to the
workspace folder with basic metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yt_dlp

from config.settings import MIN_DURATION_SECONDS, PIPELINE_WORKSPACE
from utils.ffmpeg import FFprobeError, get_video_duration
from utils.logger import get_logger

log = get_logger(__name__)

METADATA_FILENAME = "metadata.json"


class IngestionError(Exception):
    """Raised when video ingestion fails."""


def validate_duration(
    duration_seconds: float,
    *,
    skip_check: bool = False,
    min_duration: float = MIN_DURATION_SECONDS,
) -> None:
    """Raise IngestionError if the video is too short.

    Pass ``skip_check=True`` during development to allow short test clips.
    """
    if skip_check:
        log.info("Duration check skipped (dev mode)")
        return
    if duration_seconds < min_duration:
        raise IngestionError(
            f"Video is only {duration_seconds:.0f}s "
            f"({duration_seconds / 60:.1f} min) — minimum is "
            f"{min_duration / 60:.0f} min. Pass skip_duration_check=True "
            f"to override during development."
        )


def ingest(
    url: str,
    *,
    skip_duration_check: bool = False,
) -> dict[str, Any]:
    """Run Stage 1 of the pipeline.

    1. Use yt-dlp to extract the video ID and download the video.
    2. Save it to ``pipeline_workspace/<video_id>/``.
    3. Probe the duration with ffprobe and validate it.
    4. Write ``metadata.json`` and return the metadata dict.

    If ``metadata.json`` already exists the stage is skipped (cache hit).
    """
    video_id = _extract_video_id(url)
    workspace = PIPELINE_WORKSPACE / video_id
    metadata_path = workspace / METADATA_FILENAME

    if metadata_path.exists():
        log.info("Stage 1 cache hit — loading existing metadata for %s", video_id)
        cached: dict[str, Any] = json.loads(metadata_path.read_text())
        return cached

    log.info("Stage 1 — ingesting video (id=%s)", video_id)
    workspace.mkdir(parents=True, exist_ok=True)

    video_path = _download_video(url, workspace)

    try:
        duration = get_video_duration(video_path)
    except FFprobeError as exc:
        raise IngestionError(str(exc)) from exc

    validate_duration(duration, skip_check=skip_duration_check)

    metadata: dict[str, Any] = {
        "video_id": video_id,
        "source": url,
        "video_filename": video_path.name,
        "duration_seconds": duration,
        "workspace": str(workspace),
    }

    metadata_path.write_text(json.dumps(metadata, indent=2))
    log.info("Stage 1 complete — metadata saved to %s", metadata_path)
    return metadata


# ── Private helpers ─────────────────────────────────────────────────────────


def _extract_video_id(url: str) -> str:
    """Ask yt-dlp for the video ID without downloading anything."""
    try:
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(url, download=False)
            video_id: str = info["id"]  # type: ignore[index]
            return video_id
    except Exception as exc:
        raise IngestionError(f"Could not extract video ID from URL: {exc}") from exc


def _download_video(url: str, workspace: Path) -> Path:
    """Download a YouTube video into *workspace* using yt-dlp.

    Returns the path to the downloaded file.
    """
    log.info("Downloading: %s", url)
    output_template = str(workspace / "%(title)s.%(ext)s")
    ydl_opts: dict[str, Any] = {
        "outtmpl": output_template,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
    except yt_dlp.utils.DownloadError as exc:
        raise IngestionError(f"yt-dlp download failed: {exc}") from exc

    video_path = Path(filename)
    if not video_path.exists():
        video_path = video_path.with_suffix(".mp4")

    if not video_path.exists():
        raise IngestionError(f"Download appeared to succeed but file not found at {video_path}")

    log.info("Downloaded to %s", video_path)
    return video_path
