"""Query REPL — pick an ingested game and generate highlights from natural language."""

from __future__ import annotations

import sys
from pathlib import Path

from config.settings import PIPELINE_WORKSPACE
from models.events import AlignedEvent
from models.game import GameState
from pipeline.clip_builder import ClipBuilderError, build_highlights
from pipeline.event_filter import filter_events
from pipeline.query_interpreter import QueryInterpreterError, interpret_query
from utils.game_registry import GameRegistry
from utils.logger import setup_logging
from utils.storage import LocalStorage, StorageBackend


def _make_storage() -> LocalStorage:
    return LocalStorage(root=PIPELINE_WORKSPACE)


def _prompt(msg: str, default: str = "") -> str:
    try:
        value = input(msg).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return value or default


def _display_game_list(games: list[GameState]) -> None:
    print()
    for i, g in enumerate(games, 1):
        print(f"  [{i}] {g.home_team} vs {g.away_team}  |  {g.league}  |  {g.date}")
    print()


def _load_aligned_events(game: GameState, storage: StorageBackend) -> list[AlignedEvent]:
    data = storage.read_json(game.video_id, "aligned_events.json")
    return [AlignedEvent.from_dict(e) for e in data.get("events", [])]


def _game_repl(game: GameState, storage: StorageBackend) -> None:
    """Inner REPL for a chosen game. Returns when user types 'back'."""
    print(f"\n  {game.home_team} vs {game.away_team} — {game.date}")
    print("  Type your highlights request, 'back' to pick another game, or 'quit'.\n")

    aligned_events = _load_aligned_events(game, storage)

    while True:
        raw = _prompt("> ")
        if raw.lower() in ("quit", "exit", "q"):
            print("Bye!")
            sys.exit(0)
        if raw.lower() == "back":
            return
        if not raw:
            continue

        try:
            query = interpret_query(raw, game, aligned_events)
        except QueryInterpreterError as exc:
            print(f"  Error: {exc}", file=sys.stderr)
            continue

        print(f"  Understood: {query.query_type.value}", end="")
        if query.event_types:
            print(f" — {', '.join(et.value for et in query.event_types)}", end="")
        if query.player_name:
            print(f" — {query.player_name}", end="")
        print()

        filtered = filter_events(aligned_events, query)

        try:
            result = build_highlights(filtered, game, query, storage)
        except ClipBuilderError as exc:
            print(f"  Error building highlights: {exc}", file=sys.stderr)
            continue

        print(f"\n  Done! {Path(result['highlights_path']).name}")
        print(f"    {result['clip_count']} clips | {result['total_duration_display']} total\n")


def run() -> None:
    """Main query REPL."""
    setup_logging()
    storage = _make_storage()
    registry = GameRegistry(storage)

    games = registry.list_ready()
    if not games:
        print("\n  No ingested games found.")
        print("  Run 'python ingest.py' first to preprocess a match.\n")
        return

    print("\n  Football Highlights Generator")
    print("  " + "-" * 34)

    while True:
        _display_game_list(games)
        pick = _prompt(f"  Pick a game [1-{len(games)}] or 'quit': ")
        if pick.lower() in ("quit", "exit", "q"):
            print("Bye!")
            break
        try:
            idx = int(pick) - 1
            if 0 <= idx < len(games):
                _game_repl(games[idx], storage)
            else:
                print("  Invalid choice.")
        except ValueError:
            print("  Please enter a number.")


if __name__ == "__main__":
    run()
