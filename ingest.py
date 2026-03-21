"""Ingest entrypoint — one-time preprocessing script per game."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from config.settings import PIPELINE_WORKSPACE
from models.game import GameState
from pipeline.event_aligner import EventAlignerError, align_events
from pipeline.match_events import MatchEventsError, fetch_match_events
from pipeline.match_finder import (
    MatchFinderError,
    download_and_save,
    find_match,
    is_url,
    resolve_fixture_for_video,
)
from pipeline.transcription import TranscriptionError, transcribe
from utils.logger import setup_logging
from utils.storage import LocalStorage, StorageBackend

ConfirmKickoffsFn = Callable[[float | None, float | None], tuple[float, float]]


def _format_duration(seconds: float) -> str:
    h, remainder = divmod(int(seconds), 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"


def _parse_timestamp(raw: str) -> float | None:
    """Parse mm:ss or raw-seconds string to float seconds. Returns None on failure."""
    raw = raw.strip()
    if ":" in raw:
        parts = raw.split(":")
        try:
            return int(parts[0]) * 60 + int(parts[1])
        except (ValueError, IndexError):
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


def _pick_youtube_result(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Show YouTube search results and let the user pick one."""
    if not candidates:
        print("  No full-match videos found.")
        return None
    print(f"\n  Found {len(candidates)} full-match candidate(s):\n")
    for i, c in enumerate(candidates, 1):
        dur = _format_duration(c["duration_seconds"])
        print(f"  [{i}] {c['title']}")
        print(f"      Duration: {dur}  |  {c['url']}\n")
    choice = input(f"  Pick a video [1-{len(candidates)}], or 's' to skip: ").strip()
    if choice.lower() == "s":
        return None
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(candidates):
            return candidates[idx]
    except ValueError:
        pass
    print("  Invalid choice, using first result.")
    return candidates[0]


def _resolve_fixture_and_row(
    video_title: str,
    upload_year: int | None = None,
) -> tuple[int | None, dict[str, Any] | None]:
    """Attempt auto-resolution of fixture. Returns (fixture_id, fixture_row).

    Silently returns (None, None) on any failure — caller handles the missing fixture.
    """
    try:
        res = resolve_fixture_for_video("", video_title, upload_year=upload_year)
        if res.fixture_id and res.fixture_row:
            return res.fixture_id, res.fixture_row
        if res.fixture_id:
            return res.fixture_id, None
    except Exception:  # noqa: BLE001  # nosec B110
        pass
    return None, None


def _run_ingest(
    url: str,
    *,
    storage: StorageBackend,
    confirm_kickoffs_fn: ConfirmKickoffsFn = _confirm_kickoffs_interactive,
) -> None:
    """Core ingest logic — separated from CLI for testability."""

    # 1. Download video
    print("\n[1/5] Downloading video...")
    metadata = download_and_save(url, storage, skip_duration_check=False)
    video_id: str = metadata["video_id"]
    source = f"https://www.youtube.com/watch?v={video_id}"
    print(f"       Video ID: {video_id} ({metadata['duration_seconds'] / 60:.0f} min)")

    # Resolve fixture from video title
    video_filename = metadata.get("video_filename", "")
    fixture_id, fixture_row = _resolve_fixture_and_row(video_filename)
    if fixture_id:
        metadata["fixture_id"] = fixture_id

    # 2. Fetch events
    print("\n[2/5] Fetching match events from API-Football...")
    match_events = fetch_match_events(metadata, storage)
    print(f"       {match_events['event_count']} events retrieved")

    # 3. Transcribe
    print("\n[3/5] Transcribing commentary...")
    transcription = transcribe(metadata, storage)
    print(f"       {len(transcription.get('utterances', []))} utterances")

    # 4. Confirm kickoffs BEFORE alignment
    print("\n[4/5] Confirming kickoff timestamps...")
    kickoff_first, kickoff_second = confirm_kickoffs_fn(
        transcription.get("kickoff_first_half"),
        transcription.get("kickoff_second_half"),
    )

    # 5. Align events
    print("\n[5/5] Aligning events to video timestamps...")
    align_events(match_events, metadata, storage, kickoff_first, kickoff_second)

    # Write game.json — only after all stages succeed
    home = (fixture_row or {}).get("home_team", "")
    away = (fixture_row or {}).get("away_team", "")
    league = (fixture_row or {}).get("league", "")
    date_raw = (fixture_row or {}).get("date", "")
    date = str(date_raw)[:10] if date_raw else ""

    game = GameState(
        video_id=video_id,
        home_team=home,
        away_team=away,
        league=league,
        date=date,
        fixture_id=int(metadata.get("fixture_id") or 0),
        video_filename=video_filename,
        source=source,
        duration_seconds=metadata["duration_seconds"],
        kickoff_first_half=kickoff_first,
        kickoff_second_half=kickoff_second,
    )
    storage.write_json(video_id, "game.json", game.to_dict())
    print(f"\n  Game ingested — {home} vs {away} | {league} | {date}\n")


def run() -> None:
    """CLI entrypoint for ingest."""
    setup_logging()
    print("\n  Football Highlights — Ingest")
    print("  " + "-" * 30)
    print("  Enter a YouTube URL or match search query.\n")

    try:
        user_input = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)

    if not user_input:
        print("  Nothing entered.")
        return

    storage = LocalStorage(root=PIPELINE_WORKSPACE)

    url = user_input
    if not is_url(user_input):
        result = find_match(user_input, storage)
        candidates = result.get("candidates", [])
        chosen = _pick_youtube_result(candidates)
        if not chosen:
            print("  Cancelled.")
            return
        url = chosen["url"]

    try:
        _run_ingest(url, storage=storage)
    except KeyboardInterrupt:
        print("\nCancelled.")
    except (MatchFinderError, MatchEventsError, TranscriptionError, EventAlignerError) as exc:
        print(f"\n  Error: {exc}\n", file=sys.stderr)


if __name__ == "__main__":
    run()
