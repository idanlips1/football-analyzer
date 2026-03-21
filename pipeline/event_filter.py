"""Event filter — pure function: filters AlignedEvents by a HighlightQuery."""

from __future__ import annotations

import difflib

from models.events import AlignedEvent
from models.highlight_query import HighlightQuery, QueryType
from utils.logger import get_logger

log = get_logger(__name__)


def filter_events(
    events: list[AlignedEvent],
    query: HighlightQuery,
) -> list[AlignedEvent]:
    """Filter *events* according to *query*.

    Always returns at least one event — falls back to the full list if
    filtering produces an empty result.
    """
    if query.query_type == QueryType.FULL_SUMMARY:
        return events

    if query.query_type == QueryType.EVENT_FILTER:
        if query.event_types is None:
            log.warning("EVENT_FILTER query has no event_types — returning all events")
            return events
        filtered = [e for e in events if e.event_type in query.event_types]

    elif query.query_type == QueryType.PLAYER:
        if query.player_name is None:
            log.warning("PLAYER query has no player_name — returning all events")
            return events
        filtered = _filter_by_player(events, query.player_name)

    else:
        return events

    if not filtered:
        print(
            f"  Warning: no events matched '{query.raw_query}' — showing full highlights instead."
        )
        return events

    return filtered


def _filter_by_player(events: list[AlignedEvent], player_name: str) -> list[AlignedEvent]:
    all_players = list({e.player for e in events if e.player})
    matches = difflib.get_close_matches(player_name, all_players, n=1, cutoff=0.6)

    matched: str | None
    if matches:
        matched = matches[0]
    else:
        # Substring fallback
        lower = player_name.lower()
        matched = next((p for p in all_players if lower in p.lower()), None)

    if matched is None:
        return []
    return [e for e in events if e.player == matched]
