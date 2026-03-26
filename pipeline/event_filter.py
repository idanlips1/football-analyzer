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
        filtered = events

    elif query.query_type == QueryType.EVENT_FILTER:
        if query.event_types is None:
            log.warning("EVENT_FILTER query has no event_types — returning all events")
            filtered = events
        else:
            filtered = [e for e in events if e.event_type in query.event_types]

    elif query.query_type == QueryType.PLAYER:
        if query.player_name is None:
            log.warning("PLAYER query has no player_name — returning all events")
            filtered = events
        else:
            filtered = _filter_by_player(events, query.player_name)

    else:
        filtered = events

    filtered = _filter_by_minute_range(filtered, query)

    if not filtered:
        log.warning("No events matched '%s' — showing full highlights instead.", query.raw_query)
        return events

    return filtered


def _filter_by_minute_range(
    events: list[AlignedEvent],
    query: HighlightQuery,
) -> list[AlignedEvent]:
    """Keep only events whose match minute falls within [minute_from, minute_to]."""
    if query.minute_from is None and query.minute_to is None:
        return events

    lo = query.minute_from or 0
    hi = query.minute_to or 999

    return [e for e in events if lo <= e.minute <= hi]


def _filter_by_player(events: list[AlignedEvent], player_name: str) -> list[AlignedEvent]:
    all_names: set[str] = set()
    for e in events:
        if e.player:
            all_names.add(e.player)
        if e.assist:
            all_names.add(e.assist)

    matched_names: set[str] = set()

    close = difflib.get_close_matches(player_name, list(all_names), n=5, cutoff=0.6)
    matched_names.update(close)

    lower = player_name.lower()
    for n in all_names:
        if lower in n.lower() or n.lower() in lower:
            matched_names.add(n)

    if not matched_names:
        return []
    return [e for e in events if e.player in matched_names or e.assist in matched_names]
