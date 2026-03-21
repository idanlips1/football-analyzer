"""Stage 5b — Clip window calculation, merging, budget enforcement, and highlights assembly.

Takes aligned events (from Stage 4) and produces a highlights video by:
1. Calculating per-event clip windows (pre/post roll from config)
2. Merging overlapping or adjacent clips
3. Enforcing a total duration budget (dropping lowest-priority clips)
4. Cutting and concatenating with FFmpeg
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from config.clip_windows import get_priority, get_window
from config.settings import (
    DEFAULT_HIGHLIGHTS_DURATION_SECONDS,
    FADE_DURATION_SECONDS,
    MERGE_GAP_SECONDS,
    PIPELINE_WORKSPACE,
)
from models.events import AlignedEvent, seconds_to_timestamp
from utils.ffmpeg import FFmpegError, FFprobeError, concat_clips, cut_clip, get_video_duration
from utils.logger import get_logger

log = get_logger(__name__)

HIGHLIGHTS_FILENAME = "highlights.mp4"
MANIFEST_FILENAME = "clip_manifest.json"


class ClipBuilderError(Exception):
    """Raised when clip building fails."""


def _event_summary(event: AlignedEvent) -> str:
    """Format: ``goal 21' Trent Alexander-Arnold`` or ``goal 90+4' Darwin Nunez``."""
    time_str = f"{event.minute}+{event.extra_minute}'" if event.extra_minute else f"{event.minute}'"
    return f"{event.event_type.value} {time_str} {event.player}"


def calculate_clip_windows(
    events: list[dict[str, Any]],
    video_duration: float,
) -> list[dict[str, Any]]:
    """Compute clip start/end for each aligned event, clamped to video bounds.

    Returns clip dicts sorted by clip_start ascending.
    """
    clips: list[dict[str, Any]] = []
    for event_dict in events:
        ae = AlignedEvent.from_dict(event_dict)
        pre_roll, post_roll = get_window(ae.event_type)
        earliest_ts = min(ae.estimated_video_ts, ae.refined_video_ts)
        clip_start = max(0.0, earliest_ts - pre_roll)
        clip_end = min(video_duration, ae.refined_video_ts + post_roll)

        clips.append(
            {
                "clip_start": clip_start,
                "clip_end": clip_end,
                "events": [_event_summary(ae)],
                "event_type": ae.event_type.value,
                "priority": get_priority(ae.event_type),
            }
        )

    clips.sort(key=lambda c: c["clip_start"])
    return clips


def merge_clips(
    clips: list[dict[str, Any]],
    gap_seconds: float = MERGE_GAP_SECONDS,
) -> list[dict[str, Any]]:
    """Merge overlapping or adjacent clips (within *gap_seconds* of each other).

    Combined clips inherit the best (lowest) priority and accumulate all event
    summary strings.
    """
    if not clips:
        return []

    sorted_clips = sorted(clips, key=lambda c: c["clip_start"])
    merged: list[dict[str, Any]] = [dict(sorted_clips[0])]
    merged[0]["events"] = list(merged[0]["events"])

    for clip in sorted_clips[1:]:
        prev = merged[-1]
        if clip["clip_start"] <= prev["clip_end"] + gap_seconds:
            prev["clip_end"] = max(prev["clip_end"], clip["clip_end"])
            prev["events"].extend(clip["events"])
            prev["priority"] = min(prev["priority"], clip["priority"])
        else:
            new_clip = dict(clip)
            new_clip["events"] = list(new_clip["events"])
            merged.append(new_clip)

    return merged


def enforce_budget(
    clips: list[dict[str, Any]],
    budget_seconds: float = DEFAULT_HIGHLIGHTS_DURATION_SECONDS,
) -> list[dict[str, Any]]:
    """Drop lowest-priority clips until total duration fits *budget_seconds*.

    At least one clip is always kept even if it alone exceeds the budget.
    Result is re-sorted chronologically.
    """
    total = sum(c["clip_end"] - c["clip_start"] for c in clips)
    if total <= budget_seconds:
        return sorted(clips, key=lambda c: c["clip_start"])

    by_priority = sorted(clips, key=lambda c: c["priority"])
    selected: list[dict[str, Any]] = []
    remaining = budget_seconds

    for clip in by_priority:
        duration = clip["clip_end"] - clip["clip_start"]
        if not selected or remaining >= duration:
            selected.append(clip)
            remaining -= duration

    if not selected:
        selected.append(by_priority[0])

    selected.sort(key=lambda c: c["clip_start"])
    return selected


def build_highlights(
    aligned_events_data: dict[str, Any],
    metadata: dict[str, Any],
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Orchestrate clip building: windows → merge → budget → cut → concat.

    Cache-aware: skips work when highlights.mp4 already exists and
    *overwrite* is False.
    """
    video_id: str = metadata["video_id"]
    workspace = PIPELINE_WORKSPACE / video_id
    workspace.mkdir(parents=True, exist_ok=True)
    output_path = workspace / HIGHLIGHTS_FILENAME

    events_list: list[dict[str, Any]] = aligned_events_data.get("events", [])
    if not events_list:
        raise ClipBuilderError("No events in aligned_events_data — nothing to build")

    if output_path.exists() and not overwrite:
        log.info("Clip builder cache hit — %s already exists", HIGHLIGHTS_FILENAME)
        manifest_path = workspace / MANIFEST_FILENAME
        cached_clips: list[dict[str, Any]] = []
        if manifest_path.exists():
            cached_clips = json.loads(manifest_path.read_text())
        total_dur = sum(c["clip_end"] - c["clip_start"] for c in cached_clips)
        return {
            "highlights_path": str(output_path),
            "clip_count": len(cached_clips),
            "total_duration_seconds": total_dur,
            "total_duration_display": seconds_to_timestamp(total_dur),
            "clips": cached_clips,
        }

    video_duration: float = metadata["duration_seconds"]
    video_path = workspace / metadata["video_filename"]

    clips = calculate_clip_windows(events_list, video_duration)
    clips = merge_clips(clips)
    clips = enforce_budget(clips)

    manifest_path = workspace / MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(clips, indent=2))
    log.info("Wrote clip manifest: %d clips", len(clips))

    clips_dir = workspace / "clips"
    clips_dir.mkdir(exist_ok=True)

    clip_paths: list[Path] = []
    t_cut_start = time.monotonic()
    for i, clip_dict in enumerate(clips):
        clip_path = clips_dir / f"clip_{i:03d}.mp4"
        log.info(
            "Cutting clip %d/%d (%.1f–%.1f s)…",
            i + 1,
            len(clips),
            clip_dict["clip_start"],
            clip_dict["clip_end"],
        )
        try:
            cut_clip(
                video_path,
                clip_dict["clip_start"],
                clip_dict["clip_end"],
                clip_path,
                fade_duration=FADE_DURATION_SECONDS,
            )
        except FFmpegError as exc:
            raise ClipBuilderError(f"Failed to cut clip {i}: {exc}") from exc
        clip_paths.append(clip_path)
    log.info("All %d clips cut in %.1f s", len(clips), time.monotonic() - t_cut_start)

    log.info("Concatenating %d clips into final highlights…", len(clip_paths))
    t_concat = time.monotonic()
    try:
        concat_clips(clip_paths, output_path)
    except FFmpegError as exc:
        raise ClipBuilderError(f"Failed to concatenate clips: {exc}") from exc
    log.info("Concatenation finished in %.1f s", time.monotonic() - t_concat)

    # Use the actual file duration (accounts for keyframe-seeking) with
    # a fallback to manifest arithmetic if ffprobe fails.
    manifest_duration = sum(c["clip_end"] - c["clip_start"] for c in clips)
    try:
        total_duration = get_video_duration(output_path)
    except FFprobeError:
        log.warning("Could not probe highlights duration — using manifest estimate")
        total_duration = manifest_duration

    log.info(
        "Clip builder complete — %d clips → %s (%.0fs)",
        len(clip_paths),
        output_path.name,
        total_duration,
    )
    return {
        "highlights_path": str(output_path),
        "clip_count": len(clip_paths),
        "total_duration_seconds": total_duration,
        "total_duration_display": seconds_to_timestamp(total_duration),
        "clips": clips,
    }
