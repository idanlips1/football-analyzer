#!/usr/bin/env python3
"""Interactive operator tool: YouTube query → confirm video → add catalog entry → ingest.

This script is meant to be run by an operator (your laptop), not inside the API container.

It does the "whole flow" needed for a new curated match to become queryable:

1) Search YouTube from a free-text query (yt-dlp "ytsearch")
2) Ask you to confirm the intended video (title + duration)
3) Create/append a catalog entry in ``catalog/data/matches.json`` (if missing)
4) Download the video (yt-dlp) to a temp ``match.mp4``
5) Run ingestion (transcription → kickoff confirm → events → alignment) using the repo pipeline

Requirements:
- ffmpeg/ffprobe available on PATH
- pip install -r requirements-tools.txt
- .env or environment variables for:
  - ASSEMBLYAI_API_KEY
  - API_FOOTBALL_KEY (optional but recommended)
  - AZURE_STORAGE_CONNECTION_STRING (if ingesting to Azure storage backend)
    - If not set, this script can also fetch it via Azure CLI using:
      - AZURE_STORAGE_ACCOUNT (default: footballhlstorage)
      - AZURE_RESOURCE_GROUP (default: football-hl-rg)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from catalog.loader import CatalogMatch, get_match  # noqa: E402
from utils.logger import setup_logging  # noqa: E402
from utils.storage import StorageBackend  # noqa: E402


def _slugify(raw: str) -> str:
    s = raw.strip().lower()
    s = re.sub(r"[’'`]", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "match"


def _ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True).strip()
    return float(out)


def _fmt_duration(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _require_tools() -> None:
    for tool in ("ffprobe",):
        if shutil.which(tool) is None:
            raise RuntimeError(f"Missing required tool on PATH: {tool}")


def _azure_connection_string_via_cli() -> str:
    """Best-effort lookup using Azure CLI.

    Mirrors the approach in scripts/sync_catalog_to_blob.py.
    """
    account = os.environ.get("AZURE_STORAGE_ACCOUNT", "footballhlstorage").strip()
    rg = os.environ.get("AZURE_RESOURCE_GROUP", "football-hl-rg").strip()
    try:
        out = subprocess.check_output(
            [
                "az",
                "storage",
                "account",
                "show-connection-string",
                "--name",
                account,
                "--resource-group",
                rg,
                "--query",
                "connectionString",
                "-o",
                "tsv",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _azure_connection_string() -> str:
    s = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    if s:
        return s
    return _azure_connection_string_via_cli()


def _key_vault_name() -> str:
    return os.environ.get("KV_NAME", "").strip()


def _keyvault_secret(vault_name: str, secret_name: str) -> str:
    try:
        out = subprocess.check_output(
            [
                "az",
                "keyvault",
                "secret",
                "show",
                "--vault-name",
                vault_name,
                "--name",
                secret_name,
                "--query",
                "value",
                "-o",
                "tsv",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _ensure_api_keys_from_env_or_kv() -> None:
    """Ensure required API keys exist in env; if missing, fetch from Azure Key Vault via az."""
    # We intentionally avoid printing any secret values.
    missing = [k for k in ("ASSEMBLYAI_API_KEY", "API_FOOTBALL_KEY") if not os.environ.get(k, "").strip()]
    if not missing:
        return

    vault = _key_vault_name()
    if not vault:
        raise RuntimeError(
            f"Missing {', '.join(missing)} and KV_NAME is not set. "
            "Set KV_NAME to your Azure Key Vault name (e.g. from docs/DEPLOY.md), "
            "or export the keys directly."
        )

    mapping = {
        "ASSEMBLYAI_API_KEY": "assemblyai-api-key",
        "API_FOOTBALL_KEY": "api-football-key",
        "OPENAI_API_KEY": "openai-api-key",
        "API_KEYS": "api-keys",
    }

    for env_name in missing:
        secret_name = mapping[env_name]
        val = _keyvault_secret(vault, secret_name)
        if val:
            os.environ[env_name] = val

    still = [k for k in missing if not os.environ.get(k, "").strip()]
    if still:
        raise RuntimeError(
            f"Missing required key(s): {', '.join(still)}. "
            f"Tried Key Vault {vault!r} secrets ({', '.join(mapping[k] for k in still)}). "
            "Fix the vault name/RBAC or export the env vars."
        )


def _search_youtube(query: str, *, limit: int) -> list[dict[str, Any]]:
    import yt_dlp  # type: ignore[import-not-found]

    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "ignoreerrors": True,
        "socket_timeout": 30,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    entries = [e for e in (info.get("entries") or []) if e]
    out: list[dict[str, Any]] = []
    for e in entries:
        vid = str(e.get("id") or "")
        title = str(e.get("title") or "")
        dur = int(e.get("duration") or 0)
        uploader = str(e.get("uploader") or e.get("channel") or "")
        out.append(
            {
                "id": vid,
                "url": f"https://www.youtube.com/watch?v={vid}" if vid else "",
                "title": title,
                "duration": dur,
                "uploader": uploader,
            }
        )
    return out


def _pick_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        raise RuntimeError("No candidates found for query.")

    print("\nTop results:\n")
    for i, c in enumerate(candidates, 1):
        dur = _fmt_duration(int(c.get("duration") or 0))
        uploader = c.get("uploader") or ""
        title = c.get("title") or ""
        print(f"  [{i}] {dur}  {title}  ({uploader})")

    while True:
        raw = input("\nPick a number (or 'q' to abort): ").strip().lower()
        if raw == "q":
            raise KeyboardInterrupt
        try:
            idx = int(raw) - 1
        except ValueError:
            idx = -1
        if 0 <= idx < len(candidates):
            chosen = candidates[idx]
            dur = _fmt_duration(int(chosen.get("duration") or 0))
            print(f"\nChosen:\n  {dur}  {chosen.get('title','')}\n  {chosen.get('url','')}\n")
            ok = input("Is this the intended video? [Y/n] ").strip().lower()
            if ok in ("", "y", "yes"):
                return chosen
            print("\nOk, pick again.")
        else:
            print("Invalid selection.")


def _download_youtube(url: str, dest_dir: Path) -> Path:
    import yt_dlp  # type: ignore[import-not-found]

    tmpl = str(dest_dir / "match.%(ext)s")
    ydl_opts: dict[str, Any] = {
        "outtmpl": tmpl,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "fragment_retries": 3,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    for name in ("match.mp4", "match.mkv", "match.webm"):
        p = dest_dir / name
        if p.exists():
            return p
    mp4s = list(dest_dir.glob("match*.mp4"))
    if mp4s:
        return mp4s[0]
    raise RuntimeError("yt-dlp finished but output video not found")


def _catalog_matches_path() -> Path:
    return _ROOT / "catalog" / "data" / "matches.json"


def _load_matches_json() -> dict[str, Any]:
    path = _catalog_matches_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "matches" not in data or not isinstance(data["matches"], list):
        raise RuntimeError(f"Unexpected catalog format: {path}")
    return data


def _write_matches_json(data: dict[str, Any]) -> None:
    path = _catalog_matches_path()
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _prompt(default: str, label: str) -> str:
    raw = input(f"{label} [{default}]: ").strip()
    return raw or default


def _try_extract_upload_year(text: str) -> int | None:
    m = re.search(r"\b(19|20)\d{2}\b", text)
    if not m:
        return None
    try:
        return int(m.group(0))
    except ValueError:
        return None


def _pick_fixture_from_candidates(candidates: list[dict[str, Any]]) -> int | None:
    if not candidates:
        return None
    print("\nMultiple fixtures found:\n")
    for i, r in enumerate(candidates, 1):
        fid = r.get("fixture_id")
        date = str(r.get("date") or "")[:10]
        league = str(r.get("league_name") or r.get("league") or "")
        home = str(r.get("home_team") or r.get("home") or "")
        away = str(r.get("away_team") or r.get("away") or "")
        score = ""
        hs = r.get("home_goals")
        as_ = r.get("away_goals")
        if hs is not None and as_ is not None:
            score = f"  {hs}-{as_}"
        print(f"  [{i}] {date}  {home} vs {away}{score}  ({league})  (fixture {fid})")
    raw = input("\nPick a number (or Enter to skip): ").strip()
    if not raw:
        return None
    try:
        idx = int(raw) - 1
    except ValueError:
        print("Invalid selection.")
        return None
    if 0 <= idx < len(candidates):
        try:
            return int(candidates[idx]["fixture_id"])
        except Exception:
            return None
    print("Invalid selection.")
    return None


def _resolve_fixture_id_interactive(user_query: str, video_title: str) -> int | None:
    """Resolve a fixture_id using pipeline.match_finder.

    Only prompts the user if auto-resolution fails.
    """
    from pipeline.match_finder import resolve_fixture_for_video

    try:
        upload_year = _try_extract_upload_year(video_title)
        res = resolve_fixture_for_video(user_query, video_title, upload_year=upload_year)
    except Exception as exc:  # noqa: BLE001
        print(f"\nFixture auto-resolution failed: {exc}")
        res = None

    if res is not None and getattr(res, "fixture_id", None):
        try:
            return int(res.fixture_id)
        except Exception:
            pass

    candidates = []
    if res is not None:
        candidates = list(getattr(res, "candidates", None) or [])
    if candidates:
        picked = _pick_fixture_from_candidates(candidates)
        if picked is not None:
            return picked

    raw = input("Enter fixture_id manually (or Enter to skip): ").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        print("Invalid fixture_id (must be an integer). Skipping.")
        return None


def _upsert_catalog_entry(entry: CatalogMatch) -> None:
    """Insert or replace an entry in catalog/data/matches.json."""
    data = _load_matches_json()
    matches: list[dict[str, Any]] = data["matches"]
    replaced = False
    for i, row in enumerate(matches):
        if str(row.get("match_id", "")).strip() == entry.match_id:
            matches[i] = asdict(entry)
            replaced = True
            break
    if not replaced:
        matches.append(asdict(entry))
    _write_matches_json(data)


def _ensure_catalog_entry(match_id: str, *, defaults: dict[str, str]) -> CatalogMatch:
    existing = get_match(match_id)
    if existing is not None:
        print(f"\nCatalog entry exists: {existing.match_id} — {existing.title}")
        return existing

    print("\nCreating a new catalog entry.")
    title = defaults.get("title", match_id).strip() or match_id
    season_label = defaults.get("season_label", "").strip()
    fixture_id = None

    entry = CatalogMatch(
        match_id=match_id,
        title=title,
        # These fields are still required by the current catalog schema and pipeline,
        # but the operator script intentionally does not collect them.
        home_team="",
        away_team="",
        competition="",
        season_label=season_label,
        fixture_id=fixture_id,
    )

    _upsert_catalog_entry(entry)
    print(f"Catalog updated: added {match_id!r} to catalog/data/matches.json")
    return entry


def _storage_for_run() -> StorageBackend:
    from config.settings import (  # imported late so Key Vault-fetched env vars are visible
        AZURE_BLOB_CONTAINER_HIGHLIGHTS,
        AZURE_BLOB_CONTAINER_PIPELINE,
        AZURE_BLOB_CONTAINER_VIDEOS,
        AZURE_STORAGE_CONNECTION_STRING,
        PIPELINE_WORKSPACE,
    )
    from utils.storage import BlobStorage, LocalStorage

    # Important: don't rely on config.settings.STORAGE_BACKEND here.
    # When AZURE_STORAGE_CONNECTION_STRING isn't set, config defaults STORAGE_BACKEND to "local"
    # even if Azure CLI could fetch a connection string. For this operator script, if we can
    # obtain a connection string, we should use Azure.
    conn = AZURE_STORAGE_CONNECTION_STRING.strip() or _azure_connection_string()
    if conn:
        return BlobStorage(
            conn,
            AZURE_BLOB_CONTAINER_VIDEOS,
            AZURE_BLOB_CONTAINER_PIPELINE,
            AZURE_BLOB_CONTAINER_HIGHLIGHTS,
        )
    raise RuntimeError(
        "Refusing to run with local storage. Azure connection string not available. "
        "Set AZURE_STORAGE_CONNECTION_STRING or login with Azure CLI and set "
        "AZURE_STORAGE_ACCOUNT/AZURE_RESOURCE_GROUP for lookup. "
        "If you truly want a local run, pass --local."
    )


def _storage_for_run_local_ok(*, allow_local: bool) -> StorageBackend:
    """Select storage backend; default is Azure-only unless allow_local=True."""
    from config.settings import (  # imported late so Key Vault-fetched env vars are visible
        AZURE_BLOB_CONTAINER_HIGHLIGHTS,
        AZURE_BLOB_CONTAINER_PIPELINE,
        AZURE_BLOB_CONTAINER_VIDEOS,
        AZURE_STORAGE_CONNECTION_STRING,
        PIPELINE_WORKSPACE,
    )
    from utils.storage import BlobStorage, LocalStorage

    conn = AZURE_STORAGE_CONNECTION_STRING.strip() or _azure_connection_string()
    if conn:
        return BlobStorage(
            conn,
            AZURE_BLOB_CONTAINER_VIDEOS,
            AZURE_BLOB_CONTAINER_PIPELINE,
            AZURE_BLOB_CONTAINER_HIGHLIGHTS,
        )
    if allow_local:
        return LocalStorage(root=PIPELINE_WORKSPACE)
    return _storage_for_run()


def _confirm_kickoffs_interactive(auto_first: float | None, auto_second: float | None) -> tuple[float, float]:
    def _parse_timestamp(raw: str) -> float | None:
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

    def _confirm_one(label: str, auto: float | None) -> float:
        if auto is not None:
            mins, secs = divmod(int(auto), 60)
            answer = input(f"  {label} kickoff detected at {mins}:{secs:02d} — correct? [Y/n] ").strip().lower()
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


def _run_ingest(
    match_id: str,
    video_path: Path,
    storage: StorageBackend,
    *,
    user_query: str,
    video_title: str,
) -> None:
    from pipeline.catalog_pipeline import CatalogPipelineError
    from pipeline.event_aligner import align_events
    from pipeline.ingestion import ingest_local_catalog_match
    from pipeline.match_events import fetch_match_events
    from pipeline.transcription import transcribe
    from utils.storage import StorageError

    # If the video+metadata already exist in storage (common when re-running after a later-stage failure),
    # avoid re-uploading or re-downloading. We'll proceed from the existing blobs.
    metadata: dict[str, Any] | None = None
    try:
        existing_meta = storage.read_json(match_id, "metadata.json")
        existing_video = storage.local_path(match_id, "match.mp4")
        if existing_video.exists():
            metadata = existing_meta
            print("\n[1/7] Found existing match.mp4 + metadata.json in storage (skipping upload).")
    except StorageError:
        metadata = None

    if metadata is None:
        print("\n[1/7] Uploading video + metadata to storage…")
        metadata = ingest_local_catalog_match(match_id, video_path, storage)

    print("\n[2/7] Transcribing with AssemblyAI (this may take a while)…")
    transcription = transcribe(metadata, storage)

    print("\n[3/7] Confirming kickoffs…")
    k_first, k_second = _confirm_kickoffs_interactive(
        transcription.get("kickoff_first_half"),
        transcription.get("kickoff_second_half"),
    )

    if k_first is None or k_second is None:
        raise CatalogPipelineError("Could not confirm kickoff timestamps.")

    if metadata.get("fixture_id") is None:
        print("\n[4/7] Resolving fixture_id…")
        picked = _resolve_fixture_id_interactive(user_query, video_title)
        if picked is None:
            raise RuntimeError(
                "fixture_id is required to fetch match events (snapshots removed). "
                "Re-run and provide a fixture_id when prompted."
            )
        metadata["fixture_id"] = int(picked)
        # Persist so downstream steps (and future runs) have it.
        storage.write_json(match_id, "metadata.json", metadata)

    # Game state + events + alignment. This follows ingest.py's behavior.
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

    print("\nDone.")


def _defaults_from_title(title: str) -> dict[str, str]:
    # Best-effort only; operator does not need to confirm these.
    year = ""
    m = re.search(r"\b(19|20)\d{2}\b", title)
    if m:
        year = m.group(0)
    return {
        "title": title.strip(),
        "season_label": year,
    }


def main(argv: Iterable[str] | None = None) -> int:
    setup_logging()
    _require_tools()
    _ensure_api_keys_from_env_or_kv()

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("youtube_query", help="Free-text YouTube query (used with ytsearch)")
    p.add_argument("--limit", type=int, default=8, help="How many search results to show")
    p.add_argument("--match-id", default="", help="Catalog match_id to use (default: derived)")
    p.add_argument(
        "--no-fixture-resolve",
        action="store_true",
        help="Skip API-Football fixture auto-resolution step",
    )
    p.add_argument(
        "--local",
        action="store_true",
        help="Allow using local pipeline_workspace/ if Azure is unavailable (dangerous).",
    )
    p.add_argument("--no-ingest", action="store_true", help="Only add catalog + download (no ingest)")
    p.add_argument(
        "--resume",
        action="store_true",
        help="If video already exists in Blob, skip yt-dlp download and resume ingest.",
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    candidates = _search_youtube(args.youtube_query, limit=args.limit)
    chosen = _pick_candidate(candidates)

    title = str(chosen.get("title") or "").strip()
    dur = int(chosen.get("duration") or 0)
    url = str(chosen.get("url") or "").strip()
    if not url:
        raise RuntimeError("Chosen candidate has no URL.")

    print(f"Confirmed: {_fmt_duration(dur)} — {title}")

    suggested = args.match_id.strip() or _slugify(title[:80])
    match_id = input(f"\nCatalog match_id [{suggested}]: ").strip() or suggested

    defaults = _defaults_from_title(title)
    entry = _ensure_catalog_entry(match_id, defaults=defaults)

    if not args.no_fixture_resolve and entry.fixture_id is None:
        print("\nResolving fixture_id via API-Football (from title/query)…")
        picked = _resolve_fixture_id_interactive(args.youtube_query, title)
        if picked is not None:
            entry = CatalogMatch(
                match_id=entry.match_id,
                title=entry.title,
                home_team=entry.home_team,
                away_team=entry.away_team,
                competition=entry.competition,
                season_label=entry.season_label,
                fixture_id=int(picked),
            )
            _upsert_catalog_entry(entry)
            print(f"Catalog updated: fixture_id={picked} for {entry.match_id!r}")
        else:
            print("No fixture selected/resolved; continuing with fixture_id=None.")

    storage = _storage_for_run_local_ok(allow_local=bool(args.local))

    video_path: Path | None = None
    if args.resume:
        maybe = storage.local_path(match_id, "match.mp4")
        if maybe.exists():
            video_path = maybe
            print("\nResuming: using existing match.mp4 from storage (skipping yt-dlp download).")

    with tempfile.TemporaryDirectory() as td:
        if video_path is None:
            work = Path(td)
            print("\nDownloading video with yt-dlp…")
            raw = _download_youtube(url, work)
            video_path = work / "match.mp4"
            if raw.resolve() != video_path.resolve():
                shutil.copy2(raw, video_path)

            # Quick sanity printout for operator confidence.
            actual = int(_ffprobe_duration(video_path))
            print(f"Downloaded: match.mp4 ({_fmt_duration(actual)})")

        if args.no_ingest:
            print("\n--no-ingest set; stopping after download.")
            return 0
        _run_ingest(
            match_id,
            video_path,
            storage,
            user_query=args.youtube_query,
            video_title=title,
        )

    print("\nNext:")
    print("  - If you’re using the deployed API, redeploy after committing the catalog change.")
    print("  - Verify availability with GET /api/v1/matches, then POST /api/v1/jobs.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        raise
