"""Interactive CLI entry point for the Football Highlights Generator.

Guides the user through: match search → video download → event fetching →
transcription → event alignment → clip building.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline.clip_builder import ClipBuilderError, build_highlights
from pipeline.event_aligner import EventAlignerError, align_events
from pipeline.match_events import MatchEventsError, fetch_match_events
from pipeline.match_finder import (
    MatchFinderError,
    download_and_save,
    extract_video_id_from_url,
    fetch_video_info,
    find_match,
    is_url,
    load_existing_metadata,
    resolve_fixture_for_video,
    search_fixtures,
)
from pipeline.transcription import TranscriptionError, transcribe
from utils.logger import setup_logging


def _prompt(msg: str, default: str = "") -> str:
    """Read a line from stdin with a prompt. Returns *default* on empty input."""
    try:
        value = input(msg).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return value or default


def _format_duration(seconds: float) -> str:
    h, remainder = divmod(int(seconds), 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"


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

    choice = _prompt(f"  Pick a video [1-{len(candidates)}], or 's' to skip: ", "1")
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


def _parse_two_teams(raw: str) -> tuple[str, str] | None:
    """Split ``"Team A, Team B"`` into two names. Returns ``None`` if invalid."""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def _pick_fixture_from_list(fixtures: list[dict[str, Any]]) -> int | None:
    """Show numbered API rows and return the chosen fixture id."""
    for i, fx in enumerate(fixtures, 1):
        goals = fx.get("score")
        score_str = ""
        if isinstance(goals, dict):
            gh, ga = goals.get("home"), goals.get("away")
            if gh is not None and ga is not None:
                score_str = f" {gh}-{ga}"
        print(f"  [{i}] {fx['home_team']} vs {fx['away_team']}{score_str}")
        print(f"      {fx['league']} ({fx['date']})  id={fx['fixture_id']}\n")

    pick = _prompt(f"  Pick [1-{len(fixtures)}] or Enter to skip: ")
    if not pick:
        return None
    try:
        idx = int(pick) - 1
        if 0 <= idx < len(fixtures):
            return int(fixtures[idx]["fixture_id"])
    except ValueError:
        pass
    print("  Invalid choice, skipping.")
    return None


def _link_fixture_interactive() -> int | None:
    """Fallback: manual fixture ID, team search, or skip."""
    print("\n  Manual API-Football linking:")
    print("    [i]  Enter fixture ID (from dashboard or API)")
    print("    [s]  Search by two team names")
    print("    [Enter]  Skip — no API events (pipeline stops after download)")
    choice = _prompt("  Choice [i/s/Enter]: ").strip().lower()

    if choice == "i":
        raw = _prompt("  Fixture ID: ")
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            print("  Not a valid number.")
            return None

    if choice == "s":
        return _pick_fixture_from_team_search()

    return None


def _pick_fixture_from_team_search() -> int | None:
    """Prompt for teams and optional date/season, then pick a fixture from results."""
    raw = _prompt('  Two teams, comma-separated (e.g. "Liverpool, Arsenal"): ')
    teams = _parse_two_teams(raw)
    if not teams:
        print("  Need two names separated by a comma.")
        return None
    team1, team2 = teams

    date_str = _prompt("  Match date YYYY-MM-DD (optional, Enter to skip): ").strip()
    date = date_str or None

    default_year = datetime.now().year
    season_s = _prompt(
        f"  Season starting year (e.g. {default_year} for {default_year}–{default_year + 1}, "
        "Enter for default): "
    ).strip()
    season: int | None = None
    if season_s:
        try:
            season = int(season_s)
        except ValueError:
            print("  Ignoring invalid season — using default year.")

    fixtures = search_fixtures(team1, team2, date=date, season=season)
    if not fixtures:
        print(
            "  No fixtures found. Try different spellings, "
            "a date within your API plan limits, or another season."
        )
        raw_id = _prompt("  Enter fixture ID manually, or Enter to skip: ")
        if raw_id:
            try:
                return int(raw_id)
            except ValueError:
                print("  Not a valid number.")
        return None

    print(f"\n  Found {len(fixtures)} fixture(s):\n")
    return _pick_fixture_from_list(fixtures)


def _format_fixture_summary(row: dict[str, Any]) -> str:
    """One-line summary of a fixture row for confirmation prompts."""
    goals = row.get("score")
    score_str = ""
    if isinstance(goals, dict):
        gh, ga = goals.get("home"), goals.get("away")
        if gh is not None and ga is not None:
            score_str = f" {gh}-{ga}"
    date_str = str(row.get("date", ""))[:10]
    league = row.get("league", "")
    return f"{row['home_team']}{score_str} {row['away_team']} | {league} | {date_str}"


def _resolve_fixture_auto(
    user_query: str,
    video_title: str,
    *,
    upload_year: int | None = None,
) -> int | None:
    """Resolve fixture from title + query via API; fall back to manual prompts."""
    print("\n  Resolving fixture via API-Football…")
    res = resolve_fixture_for_video(
        user_query,
        video_title,
        upload_year=upload_year,
    )

    if res.teams_parsed:
        print(f"  Detected teams: {res.team_a} vs {res.team_b}")

    if res.fixture_id is not None:
        # Show what we matched and ask for confirmation
        if res.fixture_row:
            summary = _format_fixture_summary(res.fixture_row)
            print(f"  Auto-matched: {summary}  (id={res.fixture_id})")
            confirm = _prompt("  Is this correct? [Y/n] ", "y")
            if confirm.lower() in ("n", "no"):
                print("  Rejected — trying manual linking.")
                return _link_fixture_interactive()
        else:
            print(f"  Auto-matched fixture id={res.fixture_id}")
        return res.fixture_id

    if len(res.candidates) == 1:
        only = res.candidates[0]
        only_id = int(only["fixture_id"])
        summary = _format_fixture_summary(only)
        print(f"  Single match found: {summary}  (id={only_id})")
        confirm = _prompt("  Is this correct? [Y/n] ", "y")
        if confirm.lower() in ("n", "no"):
            print("  Rejected — trying manual linking.")
            return _link_fixture_interactive()
        return only_id

    if len(res.candidates) > 1:
        print(f"\n  {len(res.candidates)} possible fixture(s) — which one?\n")
        picked = _pick_fixture_from_list(res.candidates)
        if picked is not None:
            return picked
        print("  Trying manual linking instead.")
        return _link_fixture_interactive()

    if res.teams_parsed:
        print("  API returned no fixtures for these teams (check your API plan date limits).")
    else:
        print("  Could not parse team names from the video title.")
    return _link_fixture_interactive()


def _ask_kickoff_time(half: str) -> float | None:
    """Prompt user for a manual kickoff timestamp when auto-detection fails."""
    raw = _prompt(f"  Enter {half} kickoff time in the video (e.g. 5:30 or 330): ")
    if not raw:
        return None
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


def _check_ffmpeg() -> None:
    """Exit early with a clear message if ffmpeg/ffprobe are not on PATH."""
    for binary in ("ffmpeg", "ffprobe"):
        if shutil.which(binary) is None:
            print(f"\n  Error: '{binary}' not found on PATH.")
            print("  FFmpeg is required for downloading, cutting, and merging video.")
            print("  Install it:  brew install ffmpeg  (macOS)")
            print("                sudo apt install ffmpeg  (Ubuntu)")
            print("                https://ffmpeg.org/download.html\n")
            sys.exit(1)


def run() -> None:  # noqa: C901
    """Main interactive loop."""
    setup_logging()
    _check_ffmpeg()
    print("\n  Football Highlights Generator")
    print("  " + "-" * 34)
    print("  Enter a match (e.g. 'Champions League final 2024')")
    print("  or a YouTube URL, or 'quit' to exit.\n")

    while True:
        user_input = _prompt("> ")
        if user_input.lower() in ("quit", "exit", "q"):
            print("Bye!")
            break
        if not user_input:
            continue

        try:
            _handle_query(user_input)
        except KeyboardInterrupt:
            print("\nCancelled.")
        except (
            MatchFinderError,
            MatchEventsError,
            TranscriptionError,
            EventAlignerError,
            ClipBuilderError,
        ) as exc:
            print(f"\n  Error: {exc}\n", file=sys.stderr)


def _step1_get_video(user_input: str) -> dict[str, Any] | None:
    """Resolve or download the match video (Step 1).

    Returns metadata dict, or ``None`` if the user cancelled.
    When the video is already downloaded, skips all network calls and returns
    the cached metadata immediately.
    """
    # Fast path: if the URL points to an already-downloaded video, skip
    # all network calls (yt-dlp info, fixture resolution, download).
    if is_url(user_input):
        vid_id = extract_video_id_from_url(user_input)
        cached = load_existing_metadata(vid_id) if vid_id else None
        if cached is not None:
            print("\n[1/5] Video already downloaded — skipping.")
            if not cached.get("fixture_id"):
                fixture_id = _resolve_fixture_auto(
                    "",
                    cached.get("video_filename", ""),
                )
                if fixture_id:
                    cached["fixture_id"] = fixture_id
                    meta_path = Path(cached["workspace"]) / "metadata.json"
                    meta_path.write_text(json.dumps(cached, indent=2))
            return cached

        vinfo = fetch_video_info(user_input)
        fixture_id = _resolve_fixture_auto(
            "",
            vinfo.title,
            upload_year=vinfo.upload_year,
        )
        print("\n[1/5] Downloading video...")
        return download_and_save(
            user_input,
            fixture_id=fixture_id,
            skip_duration_check=False,
        )

    # Text query path: search YouTube, pick a result, download.
    result = find_match(user_input)
    candidates = result.get("candidates", [])
    chosen = _pick_youtube_result(candidates)
    if not chosen:
        url_fallback = _prompt("\n  Enter a YouTube URL manually (or Enter to cancel): ")
        if not url_fallback:
            return None
        chosen = {"url": url_fallback, "video_id": "", "title": url_fallback}

    fixture_id = _resolve_fixture_auto(user_input, chosen.get("title") or "")
    print("\n[1/5] Downloading video...")
    return download_and_save(
        chosen["url"],
        fixture_id=fixture_id,
        skip_duration_check=False,
    )


def _handle_query(user_input: str) -> None:  # noqa: C901
    """Process a single user query end-to-end."""
    # ------- Step 1: Find / download the match video ---------
    metadata = _step1_get_video(user_input)
    if metadata is None:
        return

    dur_min = metadata["duration_seconds"] / 60
    print(f"       Video ID: {metadata['video_id']} ({dur_min:.0f} min)")

    # ------- Step 2: Fetch match events from API-Football ----
    if not metadata.get("fixture_id"):
        print("\n  No fixture ID — skipping API-Football event fetch.")
        print("  You can re-run with a fixture ID for better highlights.")
        return

    print("\n[2/5] Fetching match events from API-Football...")
    match_events = fetch_match_events(metadata)
    evt_count = match_events["event_count"]
    print(f"       {evt_count} events retrieved")

    # ------- Step 3: Transcribe + detect kickoff -------------
    print("\n[3/5] Transcribing commentary & detecting kickoff...")
    transcription = transcribe(metadata)
    utt_count = len(transcription.get("utterances", []))
    ko1 = transcription.get("kickoff_first_half")
    ko2 = transcription.get("kickoff_second_half")
    print(f"       {utt_count} utterances transcribed")

    if ko1 is not None:
        print(f"       First half kickoff detected at {_format_duration(ko1)}")
    else:
        print("       Could not auto-detect first half kickoff.")
        ko1 = _ask_kickoff_time("first half")
        if ko1 is not None:
            transcription["kickoff_first_half"] = ko1

    if ko2 is not None:
        print(f"       Second half kickoff detected at {_format_duration(ko2)}")
    else:
        print("       Could not auto-detect second half kickoff.")
        ko2 = _ask_kickoff_time("second half")
        if ko2 is not None:
            transcription["kickoff_second_half"] = ko2

    if transcription.get("kickoff_first_half") is None:
        print("\n  Error: Cannot proceed without kickoff timestamps.", file=sys.stderr)
        return
    if transcription.get("kickoff_second_half") is None:
        print("\n  Error: Cannot proceed without kickoff timestamps.", file=sys.stderr)
        return

    # ------- Step 4: Align events to video timestamps --------
    print("\n[4/5] Aligning events to video timestamps...")
    aligned = align_events(match_events, transcription, metadata)
    aligned_count = aligned["event_count"]
    print(f"       {aligned_count} events aligned to video positions")

    # ------- Step 5: Cut clips & assemble highlights ---------
    print("\n[5/5] Cutting clips & assembling highlights...")
    result = build_highlights(aligned, metadata)

    print("\n  Done! Highlights saved to:")
    print(f"    {result['highlights_path']}")
    print(f"    {result['clip_count']} clips | {result['total_duration_display']} total\n")


if __name__ == "__main__":
    run()
