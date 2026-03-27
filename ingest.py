"""Interactive CLI: copy a local catalog match video into storage, then run preprocess.

Place a ``.mp4`` on disk (download with yt-dlp outside this repo if needed), pick a
catalog ``match_id``, and this script runs transcription → kickoff confirm → fixture
pick (optional) → ``game.json`` → events → alignment. For cloud uploads use
``scripts/upload_catalog_match.py``.
"""

from __future__ import annotations

import difflib
import json
import re
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from catalog.loader import get_match as _get_catalog_match
from catalog.loader import list_matches
from config.settings import (
    API_FOOTBALL_BASE_URL,
    API_FOOTBALL_KEY,
    AZURE_BLOB_CONTAINER_HIGHLIGHTS,
    AZURE_BLOB_CONTAINER_PIPELINE,
    AZURE_BLOB_CONTAINER_VIDEOS,
    AZURE_STORAGE_CONNECTION_STRING,
    PIPELINE_WORKSPACE,
    STORAGE_BACKEND,
)
from pipeline.catalog_pipeline import CatalogPipelineError
from pipeline.event_aligner import align_events
from pipeline.ingestion import IngestionError, ingest_local_catalog_match
from pipeline.match_events import fetch_match_events
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


def _pick_fixture_interactive(
    home_team: str,
    away_team: str,
    season_label: str,
) -> int | None:
    """Search API-Football for fixtures matching these teams/season. Operator picks.

    Returns the selected fixture_id, or None if aborted or not found.
    """
    if not API_FOOTBALL_KEY:
        print("  API_FOOTBALL_KEY not set — cannot search for fixture.")
        return None

    def _search_team(name: str) -> int | None:
        url = f"{API_FOOTBALL_BASE_URL}/teams?search={urllib.parse.quote(name)}"
        req = urllib.request.Request(
            url,
            headers={
                "x-rapidapi-key": API_FOOTBALL_KEY,
                "x-rapidapi-host": "v3.football.api-sports.io",
            },
        )
        try:
            with urllib.request.urlopen(req) as resp:  # nosec B310
                body = json.loads(resp.read().decode())
        except Exception as exc:
            print(f"  Team search failed for {name!r}: {exc}")
            return None
        results = body.get("response", [])
        if not results:
            print(f"  No team found for {name!r}.")
            return None
        candidates = [(r["team"]["id"], r["team"]["name"]) for r in results]
        name_lower = name.lower()
        for tid, tname in candidates:
            if name_lower in tname.lower() or tname.lower() in name_lower:
                return int(tid)
        close = difflib.get_close_matches(name, [t for _, t in candidates], n=1, cutoff=0.6)
        if close:
            return int(next(tid for tid, t in candidates if t == close[0]))
        return int(candidates[0][0])

    home_id = _search_team(home_team)
    away_id = _search_team(away_team)
    if home_id is None or away_id is None:
        return None

    year_match = re.search(r"\d{4}", season_label)
    season_year = year_match.group() if year_match else ""

    url = f"{API_FOOTBALL_BASE_URL}/fixtures/headtohead?h2h={home_id}-{away_id}"
    if season_year:
        url += f"&season={season_year}"
    req = urllib.request.Request(
        url,
        headers={
            "x-rapidapi-key": API_FOOTBALL_KEY,
            "x-rapidapi-host": "v3.football.api-sports.io",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:  # nosec B310
            body = json.loads(resp.read().decode())
    except Exception as exc:
        print(f"  Fixture search failed: {exc}")
        return None

    fixtures = body.get("response", [])
    if not fixtures:
        print("  No fixtures found for these teams/season.")
        return None

    if len(fixtures) == 1:
        f = fixtures[0]
        fid = f["fixture"]["id"]
        date = f["fixture"].get("date", "")[:10]
        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]
        league = f["league"]["name"]
        print(f"\n  Found: [{fid}] {date}  {home} vs {away}  ({league})")
        ans = input("  Use this fixture? [Y/n] ").strip().lower()
        if ans in ("", "y", "yes"):
            return int(fid)
        return None

    print("\n  Multiple fixtures found:\n")
    for i, f in enumerate(fixtures, 1):
        fid = f["fixture"]["id"]
        date = f["fixture"].get("date", "")[:10]
        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]
        league = f["league"]["name"]
        print(f"  [{i}] {date}  {home} vs {away}  ({league})  (fixture {fid})")
    raw = input("\n  Pick a number (or 'q' to abort): ").strip()
    if raw.lower() == "q":
        return None
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(fixtures):
            return int(fixtures[idx]["fixture"]["id"])
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
    entry = _get_catalog_match(match_id)

    path_raw = input("  Path to local full-match .mp4 or YouTube URL: ").strip()
    if not path_raw:
        print("  No path given.")
        return

    if path_raw.startswith("http://") or path_raw.startswith("https://"):
        print("\n[1/7] Downloading from YouTube (yt-dlp)…")
        import tempfile

        from scripts.upload_catalog_match import _download_youtube

        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            video_path = _download_youtube(path_raw, work)
            print("\n      Copying video into workspace/Azure…")
            metadata = ingest_local_catalog_match(match_id, video_path, storage)
    else:
        video_path = Path(path_raw).expanduser()
        print("\n[1/7] Copying video into workspace/Azure…")
        metadata = ingest_local_catalog_match(match_id, video_path, storage)

    print("\n[2/7] Transcribing with AssemblyAI (this may take a while)…")
    from pipeline.transcription import transcribe

    transcription = transcribe(metadata, storage)

    print("\n[3/7] Confirming kickoffs…")
    k_first, k_second = confirm_kickoffs_fn(
        transcription.get("kickoff_first_half"),
        transcription.get("kickoff_second_half"),
    )

    if k_first is None or k_second is None:
        raise CatalogPipelineError("Could not confirm kickoff timestamps.")

    print("\n[4/7] Searching API-Football for fixture…")
    picked = _pick_fixture_interactive(
        metadata["home_team"],
        metadata["away_team"],
        metadata.get("season_label", ""),
    )
    if picked is None:
        print("  No fixture selected — game.json will have fixture_id=None.")
    else:
        print(f"  Fixture {picked} selected.")
        metadata["fixture_id"] = picked

    print("\n[5/7] Writing game.json…")
    from models.game import GameState

    fid_raw = metadata.get("fixture_id")
    game = GameState(
        video_id=match_id,
        home_team=metadata["home_team"],
        away_team=metadata["away_team"],
        league=metadata["competition"],
        date=metadata["season_label"],
        fixture_id=int(fid_raw) if fid_raw is not None else None,
        video_filename=metadata.get("video_filename", "match.mp4"),
        source=str(metadata.get("source", f"catalog:{match_id}")),
        duration_seconds=float(metadata["duration_seconds"]),
        kickoff_first_half=float(k_first),
        kickoff_second_half=float(k_second),
    )
    storage.write_json(match_id, "game.json", game.to_dict())

    print("\n[6/7] Fetching match events…")
    events_data = fetch_match_events(metadata, storage)
    print(f"      {events_data.get('event_count', 0)} events loaded.")

    print("\n[7/7] Aligning events to video…")
    align_events(
        events_data,
        metadata,
        storage,
        k_first,
        k_second,
        force_recompute=False,
        save_to_disk=True,
    )
    print("      aligned_events.json written.")

    print(
        "\n  Done — game.json, match_events.json, and aligned_events.json written. "
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
