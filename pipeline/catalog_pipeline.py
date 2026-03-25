"""Run highlights pipeline for a curated catalog match (video in blob storage)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from catalog.loader import CatalogMatch, get_match
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


def run_catalog_stages_to_game_json(
    match_id: str,
    storage: StorageBackend,
    progress_callback: Any = None,
    kickoff_first_override: float | None = None,
    kickoff_second_override: float | None = None,
    kickoff_fn: Callable[[float | None, float | None], tuple[float, float]] | None = None,
) -> tuple[CatalogMatch, dict[str, Any], str]:
    """Events → transcription → alignment → ``game.json`` (no clips)."""
    entry = get_match(match_id)
    if entry is None:
        raise CatalogPipelineError(f"Unknown match_id: {match_id!r}")

    if progress_callback:
        progress_callback("loading_video")

    metadata = merge_catalog_metadata(storage, entry)
    ensure_video_file_exists(storage, metadata)

    video_id = str(metadata["video_id"])

    if progress_callback:
        progress_callback("fetching_events")

    from pipeline.match_events import fetch_match_events

    match_events = fetch_match_events(metadata, storage)

    if progress_callback:
        progress_callback("transcribing")

    from pipeline.transcription import transcribe

    transcription = transcribe(metadata, storage)

    if kickoff_fn is not None:
        k_first, k_second = kickoff_fn(
            transcription.get("kickoff_first_half"),
            transcription.get("kickoff_second_half"),
        )
        kickoff_first: float | None = k_first
        kickoff_second: float | None = k_second
    else:
        kickoff_first = (
            kickoff_first_override
            if kickoff_first_override is not None
            else transcription.get("kickoff_first_half")
        )
        kickoff_second = (
            kickoff_second_override
            if kickoff_second_override is not None
            else transcription.get("kickoff_second_half")
        )

    if kickoff_first is None or kickoff_second is None:
        raise CatalogPipelineError(
            "Could not auto-detect kickoff timestamps. "
            "Re-submit with kickoff_first_half and kickoff_second_half overrides."
        )
    k1 = float(kickoff_first)
    k2 = float(kickoff_second)

    if progress_callback:
        progress_callback("aligning")

    from pipeline.event_aligner import align_events

    align_events(match_events, metadata, storage, k1, k2)

    from models.game import GameState

    game = GameState(
        video_id=video_id,
        home_team=entry.home_team,
        away_team=entry.away_team,
        league=entry.competition,
        date=entry.season_label,
        fixture_id=int(metadata.get("fixture_id") or 0),
        video_filename=metadata.get("video_filename", ""),
        source=str(metadata.get("source", f"catalog:{video_id}")),
        duration_seconds=float(metadata["duration_seconds"]),
        kickoff_first_half=k1,
        kickoff_second_half=k2,
    )
    storage.write_json(video_id, "game.json", game.to_dict())

    return entry, metadata, video_id


def run_catalog_pipeline(
    match_id: str,
    highlights_query: str,
    storage: StorageBackend,
    progress_callback: Any = None,
    kickoff_first_override: float | None = None,
    kickoff_second_override: float | None = None,
) -> dict[str, Any]:
    """Stages 2–5: events → transcription → alignment → clips for a catalog match."""
    _entry, _metadata, video_id = run_catalog_stages_to_game_json(
        match_id,
        storage,
        progress_callback=progress_callback,
        kickoff_first_override=kickoff_first_override,
        kickoff_second_override=kickoff_second_override,
        kickoff_fn=None,
    )

    if progress_callback:
        progress_callback("building_clips")

    from models.events import AlignedEvent
    from models.game import GameState
    from models.highlight_query import HighlightQuery, QueryType
    from pipeline.clip_builder import build_highlights
    from pipeline.event_filter import filter_events

    game = GameState.from_dict(storage.read_json(video_id, "game.json"))

    aligned_data = storage.read_json(video_id, "aligned_events.json")
    aligned_events = [AlignedEvent.from_dict(e) for e in aligned_data.get("events", [])]

    try:
        from pipeline.query_interpreter import interpret_query

        hq = interpret_query(highlights_query, game, aligned_events)
    except Exception:  # noqa: BLE001
        hq = HighlightQuery(query_type=QueryType.FULL_SUMMARY, raw_query=highlights_query)

    filtered = filter_events(aligned_events, hq)
    result = build_highlights(
        filtered,
        game,
        hq,
        storage,
        confirm_overwrite_fn=lambda _path: False,
    )
    result["video_id"] = video_id
    return result
