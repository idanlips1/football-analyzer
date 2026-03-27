"""Stage 1 — Place a local match video in storage for a catalog match_id.

Production video acquisition uses Azure Blob + :mod:`pipeline.catalog_pipeline`.
Use :func:`ingest_local_catalog_match` for local runs; operators upload with
``scripts/upload_catalog_match.py`` (optional yt-dlp on the operator machine only).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from config.settings import MIN_DURATION_SECONDS, PIPELINE_WORKSPACE
from utils.ffmpeg import FFprobeError, get_video_duration
from utils.logger import get_logger
from utils.storage import StorageBackend

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


def ingest_local_catalog_match(
    match_id: str,
    source_mp4: Path,
    storage: StorageBackend,
    *,
    skip_duration_check: bool = False,
) -> dict[str, Any]:
    """Copy a local ``.mp4`` into storage as ``<match_id>/match.mp4`` and write metadata.

    ``match_id`` becomes the storage folder key (e.g. ``videos/<match_id>/match.mp4`` on Azure).
    """
    src = source_mp4.expanduser().resolve()
    if not src.is_file():
        raise IngestionError(f"Not a file: {src}")
    if src.suffix.lower() != ".mp4":
        raise IngestionError("Source must be a .mp4 file")

    video_id = match_id.strip()
    if not video_id:
        raise IngestionError("match_id is empty")
    storage.workspace_path(video_id)
    storage.upload_file(video_id, "match.mp4", src)
    dest = storage.local_path(video_id, "match.mp4")

    try:
        duration = get_video_duration(dest)
    except FFprobeError as exc:
        raise IngestionError(str(exc)) from exc

    validate_duration(duration, skip_check=skip_duration_check)

    metadata: dict[str, Any] = {
        "video_id": video_id,
        "source": f"catalog:{video_id}",
        "video_filename": "match.mp4",
        "duration_seconds": duration,
        "fixture_id": None,
        "home_team": "",
        "away_team": "",
        "competition": "",
        "season_label": "",
    }
    storage.write_json(video_id, "metadata.json", metadata)
    log.info("Stage 1 complete — %s → %s", src, dest)
    return metadata


def default_workspace_storage() -> StorageBackend:
    """Local storage rooted at :data:`config.settings.PIPELINE_WORKSPACE`."""
    from utils.storage import LocalStorage

    return LocalStorage(root=PIPELINE_WORKSPACE)
