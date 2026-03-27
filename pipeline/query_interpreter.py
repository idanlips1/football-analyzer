"""Query interpreter — converts natural language to HighlightQuery via OpenAI."""

from __future__ import annotations

import json
from typing import cast

from openai import OpenAI

from config.settings import OPENAI_API_KEY, OPENAI_LABEL_MODEL, OPENAI_MODEL
from models.events import EventType
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
  "player_name": "exact player name from the provided list" | null,
  "minute_from": integer | null,
  "minute_to": integer | null
}

Valid event_type strings:
  goal, own_goal, penalty, red_card, yellow_card, var_review,
  card, near_miss, save, shot_on_target, free_kick, corner, substitution, other

Rules:
- General / full-match queries (e.g. "best moments", "highlights") → full_summary
- Event-type queries (e.g. "just goals", "cards and VAR") → event_filter + event_types
- Player queries (e.g. "Salah moments") → player + player_name (exact name from list)
  Player highlights include ALL events involving that player — goals, cards,
  substitutions, assists — not just goals.

Time / half filtering (applies to ANY query_type above):
- "first half" → minute_from=1, minute_to=45
- "second half" → minute_from=46, minute_to=90
- "last 10 minutes" → minute_from=80, minute_to=90
- Explicit ranges like "between 20 and 60 minutes" → minute_from=20, minute_to=60
- If no time constraint is mentioned, leave both null.
- Extra-time minutes (e.g. 45+2) count under the half they belong to:
  45+anything is first half (≤45), 90+anything is second half (≤90).

Return ONLY valid JSON, nothing else.\
"""

_LABEL_SYSTEM_PROMPT = (
    "Return ONLY a short snake_case label (1–3 words, no articles) for a football "
    "highlights video described by the user query. Examples: 'neto_highlights', "
    "'second_half_goals', 'all_corners', 'full_match'. No explanation, just the label."
)


def _generate_highlights_label(raw_query: str, client: OpenAI) -> str:
    """Ask a cheap model for a short human-readable slug from *raw_query*.

    Returns "" on any failure so callers can fall back gracefully.
    """
    try:
        resp = client.chat.completions.create(
            model=OPENAI_LABEL_MODEL,
            messages=[
                {"role": "system", "content": _LABEL_SYSTEM_PROMPT},
                {"role": "user", "content": raw_query},
            ],
            temperature=0,
            max_tokens=20,
        )
        label = (resp.choices[0].message.content or "").strip().strip("'\"")
        log.debug("LLM label for %r → %r", raw_query, label)
        return label
    except Exception as exc:
        log.warning("Label generation failed (%s) — falling back to raw slug", exc)
        return ""


def interpret_query(
    raw_query: str,
    game: GameState,
    player_names: list[str],
) -> HighlightQuery:
    """Interpret *raw_query* using OpenAI and return a structured HighlightQuery."""
    if not OPENAI_API_KEY:
        raise QueryInterpreterError("OPENAI_API_KEY is not set — add it to your .env file")

    user_message = (
        f"Game: {game.home_team} vs {game.away_team} ({game.date})\n"
        f"Available players: {json.dumps(sorted(player_names))}\n\n"
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

        player_name: str | None = data.get("player_name")  # type: ignore[assignment]
        minute_from: int | None = data.get("minute_from")  # type: ignore[assignment]
        minute_to: int | None = data.get("minute_to")  # type: ignore[assignment]

        label = _generate_highlights_label(raw_query, client)

        return HighlightQuery(
            query_type=query_type,
            event_types=event_types,
            player_name=player_name,
            raw_query=raw_query,
            minute_from=minute_from,
            minute_to=minute_to,
            label=label,
        )
    except Exception as exc:
        log.warning("Query interpretation failed (%s) — falling back to FULL_SUMMARY", exc)
        return HighlightQuery(query_type=QueryType.FULL_SUMMARY, raw_query=raw_query)
