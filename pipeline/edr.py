"""Stage 4 — EDR (Event Detection Report) generation: scoring, merging, and clip selection."""

from __future__ import annotations

import json
from typing import Any

from config.settings import (
    DEFAULT_HIGHLIGHTS_DURATION_SECONDS,
    MAX_CLIP_DURATION_SECONDS,
    MERGE_GAP_SECONDS,
    PIPELINE_WORKSPACE,
)
from models.events import EDREntry, EventType, seconds_to_timestamp
from utils.logger import get_logger

log = get_logger(__name__)

EDR_FILENAME = "edr.json"
EXCITEMENT_FILENAME = "excitement.json"


class EDRError(Exception):
    """Raised when EDR stage fails."""


def merge_windows(
    windows: list[dict[str, Any]],
    *,
    gap_seconds: float = MERGE_GAP_SECONDS,
    max_clip_seconds: float = MAX_CLIP_DURATION_SECONDS,
) -> list[EDREntry]:
    """Merge nearby excitement windows into EDREntry clips.

    Input dicts require: start_ms, end_ms, score (0–1), event_type (str),
    keyword_hits (list[str]), energy_peak (float), video_id (str).
    Windows within gap_seconds of each other (or overlapping) are merged.
    Merged score/energy_peak = max; keyword_hits = union; event_type from
    highest-scoring window.

    If a merged clip exceeds *max_clip_seconds* it is split: only the region
    around the peak-scoring window is kept (±max_clip_seconds/2), preventing
    runaway clip lengths.
    """
    if not windows:
        return []

    sorted_wins = sorted(windows, key=lambda w: w["start_ms"])
    gap_ms = gap_seconds * 1000.0

    current: dict[str, Any] = dict(sorted_wins[0])
    current["keyword_hits"] = list(current.get("keyword_hits", []))
    current["_peak_ms"] = (current["start_ms"] + current["end_ms"]) / 2.0

    merged: list[dict[str, Any]] = []

    for w in sorted_wins[1:]:
        if w["start_ms"] - current["end_ms"] <= gap_ms:
            if w["score"] > current["score"]:
                current["event_type"] = w["event_type"]
                current["_peak_ms"] = (w["start_ms"] + w["end_ms"]) / 2.0
            current["end_ms"] = max(current["end_ms"], w["end_ms"])
            current["score"] = max(current["score"], w["score"])
            current["energy_peak"] = max(current["energy_peak"], w["energy_peak"])
            hits = set(current["keyword_hits"]) | set(w.get("keyword_hits", []))
            current["keyword_hits"] = sorted(hits)
        else:
            merged.append(current)
            current = dict(w)
            current["keyword_hits"] = list(current.get("keyword_hits", []))
            current["_peak_ms"] = (current["start_ms"] + current["end_ms"]) / 2.0

    merged.append(current)

    max_ms = max_clip_seconds * 1000.0
    result: list[EDREntry] = []
    for m in merged:
        clip_len = m["end_ms"] - m["start_ms"]
        if clip_len > max_ms:
            peak = m.get("_peak_ms", (m["start_ms"] + m["end_ms"]) / 2.0)
            half = max_ms / 2.0
            m["start_ms"] = max(m["start_ms"], peak - half)
            m["end_ms"] = min(m["end_ms"], peak + half)

        try:
            event_type = EventType(m["event_type"])
        except ValueError:
            event_type = EventType.UNKNOWN
        result.append(
            EDREntry(
                start_seconds=m["start_ms"] / 1000.0,
                end_seconds=m["end_ms"] / 1000.0,
                score=m["score"],
                event_type=event_type,
                keyword_hits=m["keyword_hits"],
                energy_peak=m["energy_peak"],
                video_id=m["video_id"],
            )
        )

    return result


def select_clips(
    entries: list[EDREntry],
    *,
    budget_seconds: float = DEFAULT_HIGHLIGHTS_DURATION_SECONDS,
) -> list[EDREntry]:
    """Greedily select highest-scoring clips within total duration budget.

    Clips whose individual duration exceeds budget are skipped (not truncated).
    Output is sorted ascending by start_seconds.
    """
    if not entries:
        return []

    by_score = sorted(entries, key=lambda e: e.score, reverse=True)
    selected: list[EDREntry] = []
    total = 0.0

    for entry in by_score:
        if entry.duration > budget_seconds:
            continue
        if total + entry.duration <= budget_seconds:
            selected.append(entry)
            total += entry.duration

    return sorted(selected, key=lambda e: e.start_seconds)


def build_edr(excitement: dict[str, Any]) -> dict[str, Any]:
    """Stage 4 orchestration. Cache-aware.

    Reads excitement.json from workspace, merges windows, selects clips within
    the default budget, writes edr.json, and returns a result dict with keys:
    video_id, workspace, clip_count, total_duration_seconds, clips.
    """
    video_id: str = excitement["video_id"]
    workspace = PIPELINE_WORKSPACE / video_id
    output_path = workspace / EDR_FILENAME

    if output_path.exists():
        log.info("Stage 4 cache hit — loading existing edr.json")
        return json.loads(output_path.read_text())  # type: ignore[no-any-return]

    excitement_path = workspace / EXCITEMENT_FILENAME
    if not excitement_path.exists():
        raise EDRError(f"excitement.json not found at {excitement_path} — run Stage 3 first")

    entries_raw: list[dict[str, Any]] = json.loads(excitement_path.read_text())

    # timestamps may be HH:MM:SS strings or raw floats
    def _to_seconds(val: str | float) -> float:
        if isinstance(val, str):
            from models.events import timestamp_to_seconds

            return timestamp_to_seconds(val)
        return float(val)

    windows: list[dict[str, Any]] = [
        {
            "start_ms": _to_seconds(e["timestamp_start"]) * 1000.0,
            "end_ms": _to_seconds(e["timestamp_end"]) * 1000.0,
            "score": e["final_score"] / 10.0,
            "event_type": e["event_type"],
            "keyword_hits": e.get("keyword_matches", []),
            "energy_peak": e.get("commentator_energy", 0.0),
            "video_id": video_id,
        }
        for e in entries_raw
        if e.get("include_in_highlights", False)
    ]

    merged = merge_windows(windows)
    selected = select_clips(merged)

    clips = [c.to_dict() for c in selected]
    total_duration = sum(c.duration for c in selected)

    result: dict[str, Any] = {
        "video_id": video_id,
        "workspace": str(workspace),
        "clip_count": len(selected),
        "total_duration_seconds": total_duration,
        "total_duration_display": seconds_to_timestamp(total_duration),
        "clips": clips,
    }

    output_path.write_text(json.dumps(result, indent=2))
    log.info("Stage 4 complete — %d clips, %.1fs total", len(selected), total_duration)
    return result
