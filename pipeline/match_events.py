"""Stage 2 (API) — Fetch match events from API-Football (api-sports.io).

Retrieves structured event data (goals, cards, substitutions, VAR) for a given
fixture and caches it as ``match_events.json`` in the video workspace folder.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any

from config.settings import API_FOOTBALL_BASE_URL, API_FOOTBALL_KEY
from models.events import EventType, MatchEvent
from utils.logger import get_logger
from utils.storage import StorageBackend

log = get_logger(__name__)

MATCH_EVENTS_FILENAME = "match_events.json"


class MatchEventsError(Exception):
    """Raised when match event fetching or parsing fails."""


# ── Public API ──────────────────────────────────────────────────────────────


def fetch_match_events(metadata: dict[str, Any], storage: StorageBackend) -> dict[str, Any]:
    """Fetch match events for a fixture and cache them.

    *metadata* must contain ``fixture_id`` for the API path.
    """
    video_id: str = metadata.get("video_id", "")

    cache_path = storage.local_path(video_id, MATCH_EVENTS_FILENAME)

    fixture_id: int | None = metadata.get("fixture_id")

    if not fixture_id:
        raise MatchEventsError("metadata is missing 'fixture_id' — cannot fetch match events")

    if cache_path.exists():
        log.info("Match events cache hit for fixture %s", fixture_id)
        cached: dict[str, Any] = storage.read_json(video_id, MATCH_EVENTS_FILENAME)
        return cached

    if not API_FOOTBALL_KEY:
        raise MatchEventsError("API_FOOTBALL_KEY is not set — add it to your .env file")

    log.info("Fetching match events for fixture %s", fixture_id)
    raw_events = _fetch_events(fixture_id)
    parsed = _parse_events(raw_events)

    result: dict[str, Any] = {
        "video_id": video_id,
        "fixture_id": fixture_id,
        "event_count": len(parsed),
        "events": [ev.to_dict() for ev in parsed],
    }

    storage.write_json(video_id, MATCH_EVENTS_FILENAME, result)
    log.info(
        "Match events saved (%d events) → %s",
        len(parsed),
        cache_path,
    )
    return result


# ── Private helpers ─────────────────────────────────────────────────────────


def _fetch_events(fixture_id: int) -> list[dict[str, Any]]:
    """GET events from the API-Football ``/fixtures/events`` endpoint."""
    url = f"{API_FOOTBALL_BASE_URL}/fixtures/events?fixture={fixture_id}"
    log.info("Requesting events from API-Football for fixture %d…", fixture_id)
    req = urllib.request.Request(
        url,
        headers={
            "x-rapidapi-key": API_FOOTBALL_KEY,
            "x-rapidapi-host": "v3.football.api-sports.io",
        },
    )

    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req) as resp:  # nosec B310
            body: dict[str, Any] = json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise MatchEventsError(f"API request failed for fixture {fixture_id}: {exc}") from exc
    elapsed = time.monotonic() - t0
    log.info("API-Football events response received in %.1f s", elapsed)
    if log.isEnabledFor(logging.DEBUG):
        log.debug("API-Football raw response body:\n%s", json.dumps(body, indent=2))

    errors = body.get("errors")
    if errors:
        raise MatchEventsError(f"API-Football returned errors: {errors}")

    events: list[dict[str, Any]] = body.get("response", [])
    log.info("API returned %d raw events", len(events))
    return events


_EVENT_TYPE_MAP: dict[tuple[str, str], EventType] = {
    ("Goal", "Normal Goal"): EventType.GOAL,
    ("Goal", "Own Goal"): EventType.OWN_GOAL,
    ("Goal", "Penalty"): EventType.PENALTY,
    ("Goal", "Missed Penalty"): EventType.OTHER,
}

_CARD_DETAIL_MAP: dict[str, EventType] = {
    "Yellow Card": EventType.YELLOW_CARD,
    "Red Card": EventType.RED_CARD,
    "Second Yellow card": EventType.RED_CARD,
}


def _map_event_type(api_type: str, detail: str) -> EventType:
    """Map an API-Football type+detail pair to our :class:`EventType`."""
    if api_type == "Goal":
        return _EVENT_TYPE_MAP.get(("Goal", detail), EventType.OTHER)
    if api_type == "Card":
        return _CARD_DETAIL_MAP.get(detail, EventType.CARD)
    if api_type == "subst":
        return EventType.SUBSTITUTION
    if api_type == "Var":
        return EventType.VAR_REVIEW
    return EventType.OTHER


def _determine_half(elapsed: int) -> str:
    """Derive match period from the elapsed minute."""
    if elapsed <= 45:
        return "1st Half"
    if elapsed <= 120:
        return "2nd Half"
    return "Extra Time"


def _build_canonical_names(raw_events: list[dict[str, Any]]) -> dict[int, str]:
    """Map each player/assist ID to a single canonical name.

    API-Football sometimes returns different spellings for the same player ID
    (e.g. "P. Neto" for goals and "Pedro Neto" for cards).  We pick the longest
    name variant as canonical — it's the most informative.
    """
    id_to_names: dict[int, set[str]] = {}
    for raw in raw_events:
        for key in ("player", "assist"):
            info = raw.get(key, {}) or {}
            pid = info.get("id")
            name = info.get("name")
            if pid and name:
                id_to_names.setdefault(pid, set()).add(name)

    return {pid: max(names, key=len) for pid, names in id_to_names.items()}


def _parse_events(raw_events: list[dict[str, Any]]) -> list[MatchEvent]:
    """Convert raw API-Football event dicts into :class:`MatchEvent` objects."""
    canonical = _build_canonical_names(raw_events)
    parsed: list[MatchEvent] = []
    for raw in raw_events:
        time_info = raw.get("time", {})
        elapsed: int = time_info.get("elapsed", 0)
        extra: int | None = time_info.get("extra")

        team_info = raw.get("team", {})
        player_info = raw.get("player", {})
        assist_info = raw.get("assist", {})

        api_type: str = raw.get("type", "")
        detail: str = raw.get("detail", "")

        player_id = player_info.get("id")
        fallback_player = player_info.get("name", "")
        player_name = canonical.get(player_id, fallback_player) if player_id else fallback_player

        assist_id = assist_info.get("id")
        raw_assist: str | None = assist_info.get("name")
        assist_name = (
            canonical.get(assist_id, raw_assist) if assist_id and raw_assist else raw_assist
        )

        parsed.append(
            MatchEvent(
                minute=elapsed,
                extra_minute=extra,
                half=_determine_half(elapsed),
                event_type=_map_event_type(api_type, detail),
                team=team_info.get("name", ""),
                player=player_name,
                assist=assist_name,
                score="",
                detail=detail,
            )
        )

    return parsed
