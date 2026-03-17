"""Stage 4b — Event filtering based on user-requested event types."""

from __future__ import annotations

import json
from typing import Any

from config.settings import PIPELINE_WORKSPACE
from models.events import EDREntry, EventType
from utils.logger import get_logger

log = get_logger(__name__)

FILTERED_EDR_FILENAME = "filtered_edr.json"
EDR_FILENAME = "edr.json"


class FilteringError(Exception):
    """Raised when filtering stage fails."""


def filter_by_type(
    entries: list[EDREntry],
    event_types: list[EventType],
) -> list[EDREntry]:
    """Pure filter. Empty event_types → pass-through (return all). Preserves order."""
    if not event_types:
        return list(entries)
    return [e for e in entries if e.event_type in event_types]


def filter_edr(
    edr_data: dict[str, Any],
    event_types: list[EventType],
) -> dict[str, Any]:
    """Stage 4b orchestration. Cache-aware.

    Reads edr.json from workspace, applies event-type filter, writes
    filtered_edr.json, and returns a result dict with the same structure as
    build_edr output but with filtered clips and recalculated stats.
    """
    video_id: str = edr_data["video_id"]
    workspace = PIPELINE_WORKSPACE / video_id
    output_path = workspace / FILTERED_EDR_FILENAME

    if output_path.exists():
        log.info("Stage 4b cache hit — loading existing filtered_edr.json")
        return json.loads(output_path.read_text())  # type: ignore[no-any-return]

    edr_path = workspace / EDR_FILENAME
    if not edr_path.exists():
        raise FilteringError(f"edr.json not found at {edr_path} — run Stage 4 first")

    edr_raw: dict[str, Any] = json.loads(edr_path.read_text())
    entries = [EDREntry.from_dict(c) for c in edr_raw["clips"]]

    filtered = filter_by_type(entries, event_types)
    clips = [c.to_dict() for c in filtered]
    total_duration = sum(c.duration for c in filtered)

    result: dict[str, Any] = {
        "video_id": video_id,
        "workspace": str(workspace),
        "clip_count": len(filtered),
        "total_duration_seconds": total_duration,
        "clips": clips,
    }

    output_path.write_text(json.dumps(result, indent=2))
    log.info("Stage 4b complete — %d clips after filtering", len(filtered))
    return result
