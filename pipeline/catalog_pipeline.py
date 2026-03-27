"""Run highlights pipeline for a curated catalog match (video in blob storage)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from catalog.loader import CatalogMatch
from utils.ffmpeg import FFprobeError, get_video_duration
from utils.logger import get_logger
from utils.storage import StorageBackend

log = get_logger(__name__)

METADATA_FILENAME = "metadata.json"


class CatalogPipelineError(Exception):
    """Raised when catalog video is missing or invalid."""


def merge_catalog_metadata(
    storage: StorageBackend,
    entry: CatalogMatch,
) -> dict[str, Any]:
    """Load ``metadata.json`` from storage and merge catalog fields."""
    video_id = entry.match_id
    meta = storage.read_json(video_id, METADATA_FILENAME)
    meta["video_id"] = video_id
    meta["home_team"] = entry.home_team
    meta["away_team"] = entry.away_team
    meta["competition"] = entry.competition
    meta["season_label"] = entry.season_label
    meta["events_snapshot"] = entry.events_snapshot
    meta["catalog_title"] = entry.title
    if entry.fixture_id is not None:
        meta["fixture_id"] = entry.fixture_id
    meta.setdefault("source", f"catalog:{video_id}")
    return meta


def ensure_video_file_exists(storage: StorageBackend, metadata: dict[str, Any]) -> Path:
    """Return path to the match video file; raise if missing or unreadable."""
    video_id = str(metadata["video_id"])
    name = str(metadata.get("video_filename") or "match.mp4")
    path = storage.local_path(video_id, name)
    if not path.exists():
        raise CatalogPipelineError(
            f"No video file at {path}. Upload this catalog match to storage first "
            f"(see scripts/upload_catalog_match.py)."
        )
    try:
        get_video_duration(path)
    except FFprobeError as exc:
        raise CatalogPipelineError(f"Video file is not readable: {path}: {exc}") from exc
    return path


def run_catalog_pipeline(
    match_id: str,
    highlights_query: str,
    storage: StorageBackend,
    progress_callback: Any = None,
) -> dict[str, Any]:
    """Execute query-time pipeline: load pre-aligned events → LLM → filter → clips."""

    from models.events import AlignedEvent
    from models.game import GameState
    from models.highlight_query import HighlightQuery, QueryType
    from pipeline.clip_builder import build_highlights
    from pipeline.event_filter import filter_events
    from pipeline.query_interpreter import interpret_query

    try:
        game = GameState.from_dict(storage.read_json(match_id, "game.json"))
    except Exception as exc:
        raise CatalogPipelineError(
            f"Missing ingestion data for {match_id}. Run ingest.py first."
        ) from exc

    if progress_callback:
        progress_callback("loading_events")

    try:
        aligned_data = storage.read_json(match_id, "aligned_events.json")
    except Exception as exc:
        raise CatalogPipelineError(
            f"Missing aligned events for {match_id}. Run ingest.py first."
        ) from exc

    aligned_events = [AlignedEvent.from_dict(e) for e in aligned_data.get("events", [])]
    if not aligned_events:
        raise CatalogPipelineError(
            f"aligned_events.json for {match_id} is empty or stale. Re-run ingest.py."
        )

    player_names = sorted({name for e in aligned_events for name in [e.player, e.assist] if name})

    if progress_callback:
        progress_callback("interpreting_query")

    try:
        hq = interpret_query(highlights_query, game, player_names)
    except Exception as exc:
        log.warning("Interpreter failed: %s", exc)
        hq = HighlightQuery(query_type=QueryType.FULL_SUMMARY, raw_query=highlights_query)

    if progress_callback:
        progress_callback("filtering")

    filtered = filter_events(aligned_events, hq)

    if progress_callback:
        progress_callback("building_clips")

    result = build_highlights(
        filtered,
        game,
        hq,
        storage,
        confirm_overwrite_fn=lambda _path: False,
    )
    result["video_id"] = match_id
    return result
