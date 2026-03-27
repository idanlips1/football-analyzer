"""HighlightQuery — structured representation of a user highlights request."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from models.events import EventType


class QueryType(StrEnum):
    FULL_SUMMARY = "full_summary"
    EVENT_FILTER = "event_filter"
    PLAYER = "player"


@dataclass
class HighlightQuery:
    query_type: QueryType
    event_types: list[EventType] | None = None
    player_name: str | None = None
    raw_query: str = ""
    minute_from: int | None = None
    minute_to: int | None = None
    label: str = ""
