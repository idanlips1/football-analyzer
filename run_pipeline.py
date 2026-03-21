"""Run pipeline stages 2–5 on an existing workspace (video already downloaded).

Usage:
    python run_pipeline.py <video_id>

Resolves the fixture automatically from the cached video title using score
matching and upload-year hinting, then runs: events → transcription →
alignment → clip building.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from config.settings import PIPELINE_WORKSPACE
from pipeline.clip_builder import build_highlights
from pipeline.event_aligner import align_events
from pipeline.match_events import fetch_match_events
from pipeline.match_finder import (
    parse_video_title,
    resolve_fixture_for_video,
)
from pipeline.transcription import transcribe


def _format_duration(seconds: float) -> str:
    h, remainder = divmod(int(seconds), 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"


def _prompt(msg: str, default: str = "") -> str:
    try:
        value = input(msg).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return value or default


def _format_fixture_summary(row: dict[str, Any]) -> str:
    goals = row.get("score")
    score_str = ""
    if isinstance(goals, dict):
        gh, ga = goals.get("home"), goals.get("away")
        if gh is not None and ga is not None:
            score_str = f" {gh}-{ga}"
    date_str = str(row.get("date", ""))[:10]
    league = row.get("league", "")
    return f"{row['home_team']}{score_str} {row['away_team']} | {league} | {date_str}"


def _pick_fixture_from_list(fixtures: list[dict[str, Any]]) -> int | None:
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


def _resolve_fixture(metadata: dict[str, Any]) -> int | None:
    """Resolve fixture from the video filename stored in metadata."""
    title = metadata.get("video_filename", "")
    if not title:
        print("  No video filename in metadata — cannot auto-resolve fixture.")
        return None

    print(f"\n  Video: {title}")
    parsed = parse_video_title(title)
    if parsed:
        print(f"  Parsed teams: {parsed.team_a} vs {parsed.team_b}")
        if parsed.has_score:
            print(f"  Parsed score: {parsed.score_home}-{parsed.score_away}")

    print("  Resolving fixture via API-Football…")
    res = resolve_fixture_for_video("", title)

    if res.fixture_id is not None:
        if res.fixture_row:
            summary = _format_fixture_summary(res.fixture_row)
            print(f"  Auto-matched: {summary}  (id={res.fixture_id})")
            confirm = _prompt("  Is this correct? [Y/n] ", "y")
            if confirm.lower() in ("n", "no"):
                if res.candidates:
                    return _pick_fixture_from_list(res.candidates)
                return None
        return res.fixture_id

    if res.candidates:
        print(f"\n  {len(res.candidates)} possible fixture(s):\n")
        return _pick_fixture_from_list(res.candidates)

    print("  Could not resolve fixture automatically.")
    raw_id = _prompt("  Enter fixture ID manually (or Enter to skip): ")
    if raw_id:
        try:
            return int(raw_id)
        except ValueError:
            print("  Invalid number.")
    return None


def _ask_kickoff_time(half: str) -> float | None:
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


def run(video_id: str) -> None:
    """Run stages 2–5 for an existing workspace."""
    workspace = PIPELINE_WORKSPACE / video_id
    metadata_path = workspace / "metadata.json"

    if not metadata_path.exists():
        print(f"  Error: No metadata.json found in {workspace}", file=sys.stderr)
        sys.exit(1)

    metadata: dict[str, Any] = json.loads(metadata_path.read_text())
    dur_min = metadata["duration_seconds"] / 60
    print(f"\n  Workspace: {video_id} ({dur_min:.0f} min)")

    # ------- Resolve fixture if missing --------
    if not metadata.get("fixture_id"):
        print("  No fixture ID in metadata — resolving…")
        fixture_id = _resolve_fixture(metadata)
        if not fixture_id:
            print("  Cannot proceed without a fixture ID.", file=sys.stderr)
            sys.exit(1)
        metadata["fixture_id"] = fixture_id
        metadata_path.write_text(json.dumps(metadata, indent=2))
        print(f"  Updated metadata with fixture_id={fixture_id}")
    else:
        print(f"  Fixture ID: {metadata['fixture_id']}")

    # ------- Step 2: Fetch match events --------
    print("\n[2/5] Fetching match events from API-Football...")
    match_events = fetch_match_events(metadata)
    evt_count = match_events["event_count"]
    print(f"       {evt_count} events retrieved")

    # ------- Step 3: Transcribe + kickoff ------
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
        sys.exit(1)
    if transcription.get("kickoff_second_half") is None:
        print("\n  Error: Cannot proceed without kickoff timestamps.", file=sys.stderr)
        sys.exit(1)

    # ------- Step 4: Align events --------------
    print("\n[4/5] Aligning events to video timestamps...")
    aligned = align_events(match_events, transcription, metadata)
    aligned_count = aligned["event_count"]
    print(f"       {aligned_count} events aligned to video positions")

    # ------- Step 5: Cut clips & highlights ----
    print("\n[5/5] Cutting clips & assembling highlights...")
    result = build_highlights(aligned, metadata, overwrite=True)

    print("\n  Done! Highlights saved to:")
    print(f"    {result['highlights_path']}")
    print(f"    {result['clip_count']} clips | {result['total_duration_display']} total\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python run_pipeline.py <video_id>")
        sys.exit(1)
    run(sys.argv[1])
