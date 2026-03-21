"""Stage 4 — Align match events to video timestamps.

Maps API-Football event minutes to concrete video positions using kickoff
offsets from the transcription stage, then refines each estimate by snapping
to the best commentator utterance within a ±60 s window.

For high-excitement events (goals, penalties, own goals) the refinement
prefers the latest utterance that *precedes* the estimate — capturing the
build-up rather than the post-event reaction.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from config.settings import PIPELINE_WORKSPACE
from models.events import AlignedEvent, EventType, MatchEvent
from utils.logger import get_logger

log = get_logger(__name__)

ALIGNMENT_FILENAME = "aligned_events.json"

_SEARCH_WINDOW_SECONDS = 60.0


class EventAlignerError(Exception):
    """Raised when event alignment fails (e.g. missing kickoff data)."""


# ── Public API ──────────────────────────────────────────────────────────────


def estimate_video_timestamp(
    event: MatchEvent,
    kickoff_first: float,
    kickoff_second: float,
) -> float:
    """Convert a match minute into an approximate video timestamp (seconds).

    Uses the detected kickoff positions as anchors and adds the elapsed
    game-time delta.
    """
    if event.half == "1st Half":
        video_ts = kickoff_first + event.minute * 60
    elif event.half == "2nd Half":
        video_ts = kickoff_second + (event.minute - 45) * 60
    elif event.half == "Extra Time":
        video_ts = kickoff_second + 45 * 60 + (event.minute - 90) * 60
    else:
        video_ts = kickoff_first + event.minute * 60

    if event.extra_minute:
        video_ts += event.extra_minute * 60

    return video_ts


def refine_timestamp(
    estimated_ts: float,
    utterances: list[dict[str, Any]],
    energy_fn: Callable[[dict[str, Any]], float] | None = None,
    *,
    prefer_before: bool = False,
) -> tuple[float, float]:
    """Snap *estimated_ts* to the best utterance within ±60 s.

    When *prefer_before* is True (used for high-excitement events like goals),
    the selector favours the latest utterance that starts **before**
    *estimated_ts* — capturing the build-up rather than a post-event reaction.
    If no preceding utterance exists in the window it falls back to the
    closest one overall.

    Returns ``(refined_seconds, confidence)`` where confidence reflects
    proximity: 0.9 (≤15 s), 0.7 (≤30 s), 0.5 (≤60 s), or 0.3 if no
    utterance falls inside the window.
    """
    candidates: list[dict[str, Any]] = []
    for utt in utterances:
        utt_start_s = utt["start"] / 1000.0
        delta = abs(utt_start_s - estimated_ts)
        if delta <= _SEARCH_WINDOW_SECONDS:
            candidates.append(utt)

    if not candidates:
        return estimated_ts, 0.3

    if energy_fn is not None:
        best = max(candidates, key=energy_fn)
    elif prefer_before:
        before = [u for u in candidates if u["start"] / 1000.0 <= estimated_ts]
        if before:
            best = max(before, key=lambda u: u["start"] / 1000.0)
        else:
            best = min(
                candidates,
                key=lambda u: abs(u["start"] / 1000.0 - estimated_ts),
            )
    else:
        best = min(
            candidates,
            key=lambda u: abs(u["start"] / 1000.0 - estimated_ts),
        )

    best_start_s = best["start"] / 1000.0
    gap = abs(best_start_s - estimated_ts)

    if gap <= 15:
        confidence = 0.9
    elif gap <= 30:
        confidence = 0.7
    else:
        confidence = 0.5

    return best_start_s, confidence


# Event types where the commentator reacts *after* the action — we want to
# snap to the build-up utterance (before) rather than the reaction (after).
_PREFER_BEFORE_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.GOAL,
        EventType.OWN_GOAL,
        EventType.PENALTY,
        EventType.NEAR_MISS,
        EventType.SHOT_ON_TARGET,
    }
)


def align_events(
    match_events_data: dict[str, Any],
    transcription: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Orchestrate Stage 4: estimate → refine → filter → cache.

    Reads match events and transcription data, produces a list of
    :class:`AlignedEvent` dicts, and caches the result as
    ``aligned_events.json`` in the video workspace.
    """
    video_id: str = match_events_data.get("video_id", metadata.get("video_id", ""))
    workspace = PIPELINE_WORKSPACE / video_id
    workspace.mkdir(parents=True, exist_ok=True)
    cache_path = workspace / ALIGNMENT_FILENAME

    if cache_path.exists():
        log.info("Stage 4 cache hit — loading aligned events for %s", video_id)
        return json.loads(cache_path.read_text())  # type: ignore[no-any-return]

    kickoff_first: float | None = transcription.get("kickoff_first_half")
    kickoff_second: float | None = transcription.get("kickoff_second_half")

    if kickoff_first is None or kickoff_second is None:
        raise EventAlignerError(
            f"Missing kickoff timestamps in transcription — "
            f"first={kickoff_first}, second={kickoff_second}"
        )

    raw_events: list[dict[str, Any]] = match_events_data.get("events", [])
    utterances: list[dict[str, Any]] = transcription.get("utterances", [])

    aligned: list[AlignedEvent] = []
    for ev_dict in raw_events:
        event = MatchEvent.from_dict(ev_dict)

        if event.event_type == EventType.SUBSTITUTION:
            continue

        estimated = estimate_video_timestamp(event, kickoff_first, kickoff_second)
        refined, confidence = refine_timestamp(
            estimated,
            utterances,
            prefer_before=event.event_type in _PREFER_BEFORE_TYPES,
        )

        aligned.append(
            AlignedEvent(
                event_type=event.event_type,
                minute=event.minute,
                extra_minute=event.extra_minute,
                half=event.half,
                player=event.player,
                team=event.team,
                score=event.score,
                detail=event.detail,
                estimated_video_ts=estimated,
                refined_video_ts=refined,
                confidence=confidence,
            )
        )

    log.info("Aligned %d events (filtered substitutions) for %s", len(aligned), video_id)

    result: dict[str, Any] = {
        "video_id": video_id,
        "workspace": str(workspace),
        "event_count": len(aligned),
        "events": [a.to_dict() for a in aligned],
    }

    cache_path.write_text(json.dumps(result, indent=2))
    log.info("Stage 4 complete — saved to %s", cache_path)
    return result
