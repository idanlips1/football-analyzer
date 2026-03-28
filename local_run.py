"""Local pipeline runner — bypasses API/queue/worker for fast iteration.

Calls the catalog pipeline directly with LocalStorage. Designed for rapid
development: tweak pipeline logic, re-run, see results immediately in a
single process.

Usage examples:
    # Interactive mode — pick match and enter query
    python local_run.py

    # Direct (skip prompts)
    python local_run.py --match-id 5T7T3JbOqkE --query "show me the goals"

    # Dry-run — everything except FFmpeg cutting (fast feedback on alignment/filtering)
    python local_run.py --match-id 5T7T3JbOqkE --query "all goals" --dry-run

    # Skip OpenAI interpreter — manually specify query type
    python local_run.py --match-id 5T7T3JbOqkE --query "goals" --query-type full_summary
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
import time
from typing import Any

from config.settings import PIPELINE_WORKSPACE
from utils.logger import get_logger, setup_logging
from utils.storage import LocalStorage

log = get_logger(__name__)


def _list_local_games(storage: LocalStorage) -> list[dict[str, Any]]:
    """List matches with game.json in pipeline_workspace."""
    games: list[dict[str, Any]] = []
    for vid in storage.list_games():
        try:
            game = storage.read_json(vid, "game.json")
            games.append(game)
        except (FileNotFoundError, ValueError, TypeError, KeyError) as exc:
            log.debug("Skipping invalid local game '%s': %s", vid, exc)
    return games


def _pick_match(storage: LocalStorage) -> str | None:
    games = _list_local_games(storage)
    if not games:
        print("\n  No ingested games found in pipeline_workspace/.")
        print("  Run `python scripts/ingest_youtube_query.py` first to prepare a match.\n")
        return None
    print("\n  Available local matches:\n")
    for i, g in enumerate(games, 1):
        print(f"  [{i}] {g['video_id']}  —  {g['home_team']} vs {g['away_team']}  ({g['date']})")
    print()
    raw = input(f"  Pick a match [1-{len(games)}] or 'q': ").strip()
    if raw.lower() in ("q", "quit"):
        return None
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(games):
            return str(games[idx]["video_id"])
    except ValueError:
        pass
    print("  Invalid choice.")
    return None


def _run_pipeline_local(
    match_id: str,
    query: str,
    storage: LocalStorage,
    *,
    dry_run: bool = False,
    manual_query_type: str | None = None,
) -> dict[str, Any]:
    """Run the pipeline stages with timing and optional shortcuts."""
    from models.events import AlignedEvent
    from models.game import GameState
    from models.highlight_query import HighlightQuery, QueryType
    from pipeline.clip_builder import (
        calculate_clip_windows,
        enforce_budget,
        merge_clips,
    )
    from pipeline.event_filter import filter_events

    game = GameState.from_dict(storage.read_json(match_id, "game.json"))
    metadata = storage.read_json(match_id, "metadata.json")

    if not metadata.get("fixture_id") and game.fixture_id:
        metadata["fixture_id"] = game.fixture_id
    metadata.setdefault("home_team", game.home_team)
    metadata.setdefault("away_team", game.away_team)

    total_t0 = time.monotonic()

    print(f"\n{'=' * 60}")
    print(f"  Match: {game.home_team} vs {game.away_team} ({game.date})")
    print(f"  Query: {query!r}")
    print(f"{'=' * 60}\n")

    t0 = time.monotonic()
    print("  [1/4] Loading pre-aligned events…")
    try:
        aligned_data = storage.read_json(match_id, "aligned_events.json")
    except Exception:
        print(
            f"\n  Error: No aligned_events.json for '{match_id}'.\n"
            "  Run `python scripts/ingest_youtube_query.py` first.\n",
            file=sys.stderr,
        )
        raise
    aligned_events = [AlignedEvent.from_dict(e) for e in aligned_data.get("events", [])]
    elapsed = time.monotonic() - t0
    print(f"         → {len(aligned_events)} aligned events ({elapsed:.1f}s)\n")

    player_names = sorted({name for e in aligned_events for name in [e.player, e.assist] if name})

    t0 = time.monotonic()
    if manual_query_type:
        qt = QueryType(manual_query_type)
        player_name = query if qt == QueryType.PLAYER else None
        hq = HighlightQuery(query_type=qt, player_name=player_name, raw_query=query)
        print(f"  [2/4] Query interpretation SKIPPED (manual: {qt.value})")
    else:
        from pipeline.query_interpreter import interpret_query

        print("  [2/4] Interpreting query (OpenAI)...")
        hq = interpret_query(query, game, player_names)
    elapsed = time.monotonic() - t0
    print(f"         → type={hq.query_type.value}  events={hq.event_types}")
    print(f"           player={hq.player_name}")
    minute_range = f"{hq.minute_from}'-{hq.minute_to}'" if hq.minute_from or hq.minute_to else "all"
    print(f"           minutes={minute_range}")
    print(f"           ({elapsed:.1f}s)\n")

    t0 = time.monotonic()
    print("  [3/4] Filtering events by query...")
    filtered = filter_events(aligned_events, hq)
    elapsed = time.monotonic() - t0
    print(f"         → {len(filtered)} events after filtering ({elapsed:.1f}s)\n")

    print("  ┌─────┬────────────────┬──────────────────────┬──────────────────────┬─────────┐")
    print("  │ Min │ Type           │ Player               │ Assist               │ Video   │")
    print("  ├─────┼────────────────┼──────────────────────┼──────────────────────┼─────────┤")
    for e in filtered:
        m_str = f"{e.minute}'" if not e.extra_minute else f"{e.minute}+{e.extra_minute}'"
        m, s = divmod(int(e.refined_video_ts), 60)
        ts_str = f"{m}:{s:02d}"
        assist_str = e.assist or ""
        print(
            f"  │ {m_str:<4}│ {e.event_type.value:<15}│ {e.player:<21}│"
            f" {assist_str:<21}│ {ts_str:<8}│"
        )
    print("  └─────┴────────────────┴──────────────────────┴──────────────────────┴─────────┘\n")

    t0 = time.monotonic()
    clips = calculate_clip_windows([dataclasses.asdict(e) for e in filtered], game.duration_seconds)
    clips = merge_clips(clips)
    clips = enforce_budget(clips)
    total_dur = sum(c["clip_end"] - c["clip_start"] for c in clips)

    print(f"  [4/4] Clip plan: {len(clips)} clips, ~{total_dur:.0f}s total")
    for i, c in enumerate(clips):
        dur = c["clip_end"] - c["clip_start"]
        events_str = " + ".join(c["events"])
        print(
            f"         clip {i + 1}: {c['clip_start']:.1f}–{c['clip_end']:.1f}s"
            f" ({dur:.1f}s)  [{events_str}]"
        )

    if dry_run:
        elapsed = time.monotonic() - t0
        total_elapsed = time.monotonic() - total_t0
        print(f"\n  DRY RUN — skipping FFmpeg ({elapsed:.1f}s for planning)")
        print(f"\n  Total pipeline time: {total_elapsed:.1f}s\n")
        return {
            "highlights_path": "(dry run)",
            "clip_count": len(clips),
            "total_duration_seconds": total_dur,
            "clips": clips,
        }

    print("\n         Cutting and concatenating with FFmpeg...")
    from pipeline.clip_builder import build_highlights

    result = build_highlights(
        filtered,
        game,
        hq,
        storage,
        confirm_overwrite_fn=lambda _path: True,
    )
    elapsed = time.monotonic() - t0
    total_elapsed = time.monotonic() - total_t0
    print(f"         → {result['highlights_path']}")
    dur_s = result["total_duration_seconds"]
    print(f"           {result['clip_count']} clips, {dur_s:.0f}s ({elapsed:.1f}s)\n")
    print(f"  Total pipeline time: {total_elapsed:.1f}s\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the highlights pipeline locally (no API server needed).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--match-id", "-m", help="Match ID from pipeline_workspace/")
    parser.add_argument("--query", "-q", help="Highlights query (natural language)")
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Plan clips but skip FFmpeg (fast feedback on alignment/filtering logic)",
    )
    parser.add_argument(
        "--query-type",
        "-t",
        choices=["full_summary", "event_filter", "player"],
        help="Skip OpenAI interpreter — use this query type directly",
    )
    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Enable DEBUG logging (shows raw API response bodies, etc.)",
    )
    args = parser.parse_args()

    setup_logging()
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    storage = LocalStorage(PIPELINE_WORKSPACE)

    match_id = args.match_id
    if not match_id:
        match_id = _pick_match(storage)
        if not match_id:
            sys.exit(0)

    try:
        storage.read_json(match_id, "game.json")
    except Exception:
        print(f"\n  Error: No game.json for '{match_id}' in pipeline_workspace/.")
        print("  Run `python scripts/ingest_youtube_query.py` first.\n")
        sys.exit(1)

    query = args.query
    if not query:
        print(f"\n  Selected: {match_id}")
        try:
            query = input("  Enter highlights query: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        if not query:
            query = "full match highlights"

    try:
        _run_pipeline_local(
            match_id,
            query,
            storage,
            dry_run=args.dry_run,
            manual_query_type=args.query_type,
        )
    except KeyboardInterrupt:
        print("\n  Cancelled.")
        sys.exit(1)
    except Exception as exc:
        print(f"\n  Pipeline error: {exc}\n", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
