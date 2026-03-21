"""Stage 5b — Clip window calculation, merging, budget enforcement, and highlights assembly.

Takes aligned events (from Stage 4) and produces a highlights video by:
1. Calculating per-event clip windows (pre/post roll from config)
2. Merging overlapping or adjacent clips
3. Enforcing a total duration budget (dropping lowest-priority clips)
4. Cutting and concatenating with FFmpeg
"""

from __future__ import annotations

import dataclasses
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from config.clip_windows import get_priority, get_window
from config.settings import (
    DEFAULT_HIGHLIGHTS_DURATION_SECONDS,
    FADE_DURATION_SECONDS,
    MERGE_GAP_SECONDS,
)
from models.events import AlignedEvent, seconds_to_timestamp
from models.game import GameState
from models.highlight_query import HighlightQuery
from utils.ffmpeg import FFmpegError, FFprobeError, concat_clips, cut_clip, get_video_duration
from utils.logger import get_logger
from utils.storage import StorageBackend

log = get_logger(__name__)

HIGHLIGHTS_FILENAME = "highlights.mp4"
MANIFEST_FILENAME = "clip_manifest.json"


class ClipBuilderError(Exception):
    """Raised when clip building fails."""


ConfirmOverwriteFn = Callable[[str], bool]


def _interactive_confirm_overwrite(path: str) -> bool:
    choice = input(f"  '{path}' already exists. Overwrite? [Y/n] ").strip().lower()
    return choice in ("", "y", "yes")


def _query_slug(query: HighlightQuery) -> str:
    """Return a filesystem-safe slug from *query*.

    Non-ASCII characters (e.g. accented letters) are dropped rather than
    transliterated — this is acceptable for a local filename slug.
    Falls back to query_type.value when raw_query is empty or all-special-chars.
    """
    base = query.raw_query.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", base).strip("_")[:40]
    return slug or query.query_type.value


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
        # `not selected` is the "always keep first clip" guarantee — the highest-priority
        # clip is unconditionally included even if it alone exceeds the budget.
        if not selected or remaining >= duration:
            selected.append(clip)
            remaining -= duration

    if not selected:  # safety net: by_priority is non-empty if clips is non-empty
        selected.append(by_priority[0])

    selected.sort(key=lambda c: c["clip_start"])
    return selected


def build_highlights(
    events: list[AlignedEvent],
    game: GameState,
    query: HighlightQuery,
    storage: StorageBackend,
    *,
    confirm_overwrite_fn: ConfirmOverwriteFn = _interactive_confirm_overwrite,
) -> dict[str, Any]:
    """Orchestrate clip building: windows → merge → budget → cut → concat.

    Cache-aware: if the output file already exists, calls confirm_overwrite_fn
    to decide whether to regenerate. Returns cached result without re-cutting if
    confirm_overwrite_fn returns False.
    """
    if not events:
        raise ClipBuilderError("No events provided — nothing to build")

    workspace = storage.workspace_path(game.video_id)
    slug = _query_slug(query)
    output_path = workspace / f"highlights_{slug}.mp4"
    video_path = workspace / game.video_filename

    if output_path.exists() and not confirm_overwrite_fn(str(output_path)):
        log.info("Clip builder cache hit — %s already exists", output_path.name)
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

    # dataclasses.asdict converts StrEnum fields to their string values, which is
    # exactly what calculate_clip_windows / AlignedEvent.from_dict expect.
    clips = calculate_clip_windows([dataclasses.asdict(e) for e in events], game.duration_seconds)
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
