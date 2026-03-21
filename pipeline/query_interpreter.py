"""Query interpreter — converts natural language to HighlightQuery via OpenAI."""

from __future__ import annotations

import json
from typing import cast

from openai import OpenAI

from config.settings import OPENAI_API_KEY, OPENAI_MODEL
from models.events import AlignedEvent, EventType
from models.game import GameState
from models.highlight_query import HighlightQuery, QueryType
from utils.logger import get_logger

log = get_logger(__name__)


class QueryInterpreterError(Exception):
    """Raised on hard pre-call failures (e.g. missing API key)."""


_SYSTEM_PROMPT = """\
You are a football highlights assistant. Given a user query, return a JSON object.

JSON schema:
{
  "query_type": "full_summary" | "event_filter" | "player",
  "event_types": [list of event type strings] | null,
  "player_name": "exact player name from the provided list" | null
}

Valid event_type strings: goal, own_goal, penalty, red_card, yellow_card, var_review,
card, near_miss, save, shot_on_target, free_kick, corner, substitution, other

Rules:
- For general/summary queries → use full_summary
- For event-type queries (e.g. "just goals", "cards and VAR") → use event_filter + event_types
- For player queries (e.g. "Salah moments") → use player + player_name (exact name from list)

Return ONLY valid JSON, nothing else.\
"""


def interpret_query(
    raw_query: str,
    game: GameState,
    aligned_events: list[AlignedEvent],
) -> HighlightQuery:
    """Interpret *raw_query* using OpenAI and return a structured HighlightQuery.

    Falls back to FULL_SUMMARY on any LLM or parsing failure.
    Raises QueryInterpreterError only if OPENAI_API_KEY is missing.
    """
    if not OPENAI_API_KEY:
        raise QueryInterpreterError("OPENAI_API_KEY is not set — add it to your .env file")

    players = sorted({e.player for e in aligned_events if e.player})
    event_types_present = sorted({e.event_type.value for e in aligned_events})

    user_message = (
        f"Game: {game.home_team} vs {game.away_team} ({game.date})\n"
        f"Available players: {', '.join(players) or 'none'}\n"
        f"Event types in this match: {', '.join(event_types_present) or 'none'}\n\n"
        f"User query: {raw_query}"
    )

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0,
        )
        content = response.choices[0].message.content or ""
        data: dict[str, object] = json.loads(content)

        query_type = QueryType(str(data["query_type"]))
        event_types: list[EventType] | None = None
        raw_event_types = data.get("event_types")
        if raw_event_types:
            event_types = [EventType(et) for et in cast(list[str], raw_event_types)]

        return HighlightQuery(
            query_type=query_type,
            event_types=event_types,
            player_name=data.get("player_name"),  # type: ignore[arg-type]
            raw_query=raw_query,
        )
    except Exception as exc:
        log.warning("Query interpretation failed (%s) — falling back to FULL_SUMMARY", exc)
        return HighlightQuery(query_type=QueryType.FULL_SUMMARY, raw_query=raw_query)
