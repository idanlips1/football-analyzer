"""Interactive CLI: copy a local catalog match video into storage, then run preprocess.

Place a ``.mp4`` on disk (download with yt-dlp outside this repo if needed), pick a
catalog ``match_id``, and this script runs events → transcription → kickoff confirm
→ alignment → ``game.json``. For cloud uploads use ``scripts/upload_catalog_match.py``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from catalog.loader import list_matches
from config.settings import (
    AZURE_BLOB_CONTAINER_HIGHLIGHTS,
    AZURE_BLOB_CONTAINER_PIPELINE,
    AZURE_BLOB_CONTAINER_VIDEOS,
    AZURE_STORAGE_CONNECTION_STRING,
    PIPELINE_WORKSPACE,
    STORAGE_BACKEND,
)
from pipeline.catalog_pipeline import CatalogPipelineError
from pipeline.ingestion import IngestionError, ingest_local_catalog_match
from pipeline.transcription import TranscriptionError
from utils.logger import setup_logging
from utils.storage import BlobStorage, LocalStorage, StorageBackend

ConfirmKickoffsFn = Callable[[float | None, float | None], tuple[float, float]]


def _storage_for_cli() -> StorageBackend:
    """Use Azure Blob when :data:`STORAGE_BACKEND` is ``azure`` (default if conn string set)."""
    if STORAGE_BACKEND == "azure":
        if not AZURE_STORAGE_CONNECTION_STRING.strip():
            print(
                "  STORAGE_BACKEND is azure but AZURE_STORAGE_CONNECTION_STRING is empty.",
                file=sys.stderr,
            )
            sys.exit(1)
        return BlobStorage(
            AZURE_STORAGE_CONNECTION_STRING,
            AZURE_BLOB_CONTAINER_VIDEOS,
            AZURE_BLOB_CONTAINER_PIPELINE,
            AZURE_BLOB_CONTAINER_HIGHLIGHTS,
        )
    return LocalStorage(root=PIPELINE_WORKSPACE)


def _parse_timestamp(raw: str) -> float | None:
    """Parse h:mm:ss, mm:ss, or raw-seconds string to float seconds."""
    raw = raw.strip()
    if ":" in raw:
        parts = raw.split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
        except (ValueError, IndexError):
            return None
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _confirm_kickoffs_interactive(
    auto_first: float | None,
    auto_second: float | None,
) -> tuple[float, float]:
    """Interactive kickoff confirmation. Loops until valid timestamps entered."""

    def _confirm_one(label: str, auto: float | None) -> float:
        if auto is not None:
            mins, secs = divmod(int(auto), 60)
            answer = (
                input(f"  {label} kickoff detected at {mins}:{secs:02d} — correct? [Y/n] ")
                .strip()
                .lower()
            )
            if answer in ("", "y", "yes"):
                return auto
        else:
            print(f"  Could not auto-detect {label} kickoff.")

        while True:
            raw = input(f"  Enter {label} kickoff time (mm:ss or seconds): ").strip()
            ts = _parse_timestamp(raw)
            if ts is not None:
                return ts
            print("  Invalid format. Try e.g. '5:30' or '330'.")

    first = _confirm_one("first half", auto_first)
    second = _confirm_one("second half", auto_second)
    return first, second


def _pick_match_interactive() -> str | None:
    matches = list_matches()
    if not matches:
        print("  Catalog is empty.")
        return None
    print("\n  Curated matches:\n")
    for i, m in enumerate(matches, 1):
        print(f"  [{i}] {m['match_id']} — {m['title']}")
    raw = input("\n  Pick a number (or 'q' to quit): ").strip()
    if raw.lower() == "q":
        return None
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(matches):
            return str(matches[idx]["match_id"])
    except ValueError:
        pass
    print("  Invalid choice.")
    return None


def _run_catalog_ingest(
    match_id: str,
    storage: StorageBackend,
    *,
    confirm_kickoffs_fn: ConfirmKickoffsFn = _confirm_kickoffs_interactive,
) -> None:
    path_raw = input("  Path to local full-match .mp4 or YouTube URL: ").strip()
    if not path_raw:
        print("  No path given.")
        return

    # 1. Download/Upload video
    if path_raw.startswith("http://") or path_raw.startswith("https://"):
        print("\n[1/3] Downloading from YouTube (yt-dlp)…")
        import tempfile

        from scripts.upload_catalog_match import _download_youtube

        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            video_path = _download_youtube(path_raw, work)
            print("\n      Copying video into workspace/Azure…")
            metadata = ingest_local_catalog_match(match_id, video_path, storage)
    else:
        video_path = Path(path_raw).expanduser()
        print("\n[1/3] Copying video into workspace/Azure…")
        metadata = ingest_local_catalog_match(match_id, video_path, storage)

    # 2. Transcribe
    print("\n[2/3] Transcribing with AssemblyAI (this may take a while)…")
    from pipeline.transcription import transcribe

    transcription = transcribe(metadata, storage)

    # 3. Game JSON
    print("\n[3/3] Confirming kickoffs and creating game.json…")
    k_first, k_second = confirm_kickoffs_fn(
        transcription.get("kickoff_first_half"),
        transcription.get("kickoff_second_half"),
    )

    if k_first is None or k_second is None:
        raise CatalogPipelineError("Could not confirm kickoff timestamps.")

    from models.game import GameState

    game = GameState(
        video_id=match_id,
        home_team=metadata["home_team"],
        away_team=metadata["away_team"],
        league=metadata["competition"],
        date=metadata["season_label"],
        fixture_id=int(metadata.get("fixture_id") or 0),
        video_filename=metadata.get("video_filename", "match.mp4"),
        source=str(metadata.get("source", f"catalog:{match_id}")),
        duration_seconds=float(metadata["duration_seconds"]),
        kickoff_first_half=float(k_first),
        kickoff_second_half=float(k_second),
    )
    storage.write_json(match_id, "game.json", game.to_dict())

    print(
        "\n  Done — game.json and transcription.json written. "
        "You can now use the User CLI to query this game.\n"
    )


def run() -> None:
    """CLI entrypoint for ingest."""
    setup_logging()
    backend = "Azure Blob" if STORAGE_BACKEND == "azure" else "local workspace"
    print(f"\n  Football Highlights — Catalog ingest ({backend})")
    print("  " + "-" * 30)

    match_id = _pick_match_interactive()
    if not match_id:
        print("  Cancelled.")
        return

    storage = _storage_for_cli()

    try:
        _run_catalog_ingest(match_id, storage)
    except KeyboardInterrupt:
        print("\nCancelled.")
    except (
        IngestionError,
        CatalogPipelineError,
        TranscriptionError,
    ) as exc:
        print(f"\n  Error: {exc}\n", file=sys.stderr)


if __name__ == "__main__":
    run()
