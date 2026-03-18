"""Stage 5 — Video cutting with FFmpeg and highlights assembly."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config.settings import PIPELINE_WORKSPACE
from models.events import EDREntry, seconds_to_timestamp
from utils.ffmpeg import FFmpegError, concat_clips, cut_clip
from utils.logger import get_logger

log = get_logger(__name__)

HIGHLIGHTS_FILENAME = "highlights.mp4"


class VideoError(Exception):
    """Raised when Stage 5 fails."""


def build_highlights(
    filtered_edr: dict[str, Any],
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Stage 5 orchestration. Cache-aware.

    Cuts each selected clip from the source video using FFmpeg stream copy,
    concatenates them, and writes highlights.mp4 to the workspace.

    Returns a dict with: highlights_path, clip_count,
    total_duration_seconds, total_duration_display.
    """
    video_id: str = filtered_edr["video_id"]
    workspace = PIPELINE_WORKSPACE / video_id
    output_path = workspace / HIGHLIGHTS_FILENAME

    if output_path.exists() and not overwrite:
        log.info("Stage 5 cache hit — highlights.mp4 already exists")
        return {
            "highlights_path": str(output_path),
            "clip_count": filtered_edr["clip_count"],
            "total_duration_seconds": filtered_edr["total_duration_seconds"],
            "total_duration_display": filtered_edr["total_duration_display"],
        }

    clips_data = filtered_edr.get("clips", [])
    if not clips_data:
        raise VideoError("No clips in filtered EDR — nothing to assemble")

    metadata_path = workspace / "metadata.json"
    if not metadata_path.exists():
        raise VideoError(f"metadata.json not found at {metadata_path} — run Stage 1 first")

    metadata = json.loads(metadata_path.read_text())
    video_path = workspace / metadata["video_filename"]
    if not video_path.exists():
        raise VideoError(f"Source video not found at {video_path}")

    clips_dir = workspace / "clips"
    clips_dir.mkdir(exist_ok=True)

    clip_paths: list[Path] = []
    for i, clip_dict in enumerate(clips_data):
        entry = EDREntry.from_dict(clip_dict)
        clip_path = clips_dir / f"clip_{i:03d}.mp4"
        try:
            cut_clip(video_path, entry.start_seconds, entry.end_seconds, clip_path)
        except FFmpegError as exc:
            raise VideoError(f"Failed to cut clip {i}: {exc}") from exc
        clip_paths.append(clip_path)

    try:
        concat_clips(clip_paths, output_path)
    except FFmpegError as exc:
        raise VideoError(f"Failed to concatenate clips: {exc}") from exc

    total_duration = filtered_edr["total_duration_seconds"]
    log.info(
        "Stage 5 complete — %d clips → %s (%.0fs)",
        len(clip_paths),
        output_path.name,
        total_duration,
    )
    return {
        "highlights_path": str(output_path),
        "clip_count": len(clip_paths),
        "total_duration_seconds": total_duration,
        "total_duration_display": seconds_to_timestamp(total_duration),
    }
