"""Stage 4 — Align match events to video timestamps.

Maps API-Football event minutes to concrete video positions using kickoff
offsets from the transcription stage, then refines each estimate by snapping
to the nearest commentator utterance within a ±60 s window.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from config.settings import PIPELINE_WORKSPACE
from models.events import AlignedEvent, EventType, MatchEvent
from utils.logger import get_logger

log = get_logger(__name__)

ALIGNMENT_FILENAME = "aligned_events.json"

_SEARCH_WINDOW_SECONDS = 60.0

# Minimum confidence to keep an event in the output.  Events with no nearby
# utterance (confidence = 0.3) are kept for high-priority types; for everything
# else they add noise and are filtered out.
_MIN_CONFIDENCE_DEFAULT = 0.5
_HIGH_PRIORITY_TYPES: frozenset[EventType] = frozenset(
    {EventType.GOAL, EventType.OWN_GOAL, EventType.PENALTY, EventType.MISSED_PENALTY, EventType.RED_CARD}
)

# Words that signal commentator excitement — used to score utterances so we
# snap to the *most excited* commentary near the estimated timestamp.
_EXCITEMENT_KEYWORDS: frozenset[str] = frozenset(
    {
        "goal", "goaaal", "score", "scored", "scores",
        "penalty", "red card", "off", "sent off",
        "save", "saved", "brilliant", "incredible", "unbelievable",
        "what a", "oh no", "handball", "var", "disallowed",
        "own goal", "header", "volley", "free kick", "chance",
        "shoots", "shot", "strikes",
    }
)


def _utterance_excitement_score(utt: dict[str, Any]) -> float:
    """Score an utterance by excitement signals in its text.

    Returns a float ≥ 0; higher = more excited commentary.
    Combines:
    - Count of excitement keyword matches
    - Proportion of UPPER-CASE words (shouting)
    - Exclamation marks
    """
    text: str = utt.get("text", "")
    words = text.split()
    if not words:
        return 0.0

    # Keyword hits (case-insensitive)
    text_lower = text.lower()
    keyword_hits = sum(1 for kw in _EXCITEMENT_KEYWORDS if kw in text_lower)

    # Ratio of words that are fully upper-case (all-caps shouting)
    upper_ratio = sum(1 for w in words if re.sub(r"[^A-Za-z]", "", w).isupper() and len(w) > 1) / len(
        words
    )

    # Exclamation marks
    exclamation_count = text.count("!")

    return keyword_hits + upper_ratio * 3 + exclamation_count * 0.5


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
) -> tuple[float, float]:
    """Snap *estimated_ts* to the best utterance within ±60 s.

    Candidate selection uses each utterance's *midpoint* (not just its start)
    so that long commentary sentences announced mid-sentence are found correctly.

    When *energy_fn* is provided the highest-scoring candidate wins; otherwise
    the nearest (by midpoint distance) is chosen.

    Returns ``(refined_seconds, confidence)`` where confidence reflects
    proximity: 0.9 (≤15 s), 0.7 (≤30 s), 0.5 (≤60 s), or 0.3 if no
    utterance falls inside the window.
    The returned timestamp is always the utterance *start* (not midpoint).
    """
    candidates: list[dict[str, Any]] = []
    for utt in utterances:
        utt_start_s = utt["start"] / 1000.0
        utt_end_s = utt["end"] / 1000.0
        utt_mid_s = (utt_start_s + utt_end_s) / 2.0
        # Accept if either the start or the midpoint falls within the window
        if (
            abs(utt_start_s - estimated_ts) <= _SEARCH_WINDOW_SECONDS
            or abs(utt_mid_s - estimated_ts) <= _SEARCH_WINDOW_SECONDS
        ):
            candidates.append(utt)

    if not candidates:
        return estimated_ts, 0.3

    if energy_fn is not None:
        best = max(candidates, key=energy_fn)
    else:
        best = min(
            candidates,
            key=lambda u: abs(
                (u["start"] / 1000.0 + u["end"] / 1000.0) / 2.0 - estimated_ts
            ),
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
    video_duration: float = metadata.get("duration_seconds", float("inf"))

    # Restrict refinement to commentator utterances only to avoid snapping to
    # stadium PA, crowd noise captions, or sideline reporter utterances.
    all_utterances: list[dict[str, Any]] = transcription.get("utterances", [])
    commentator_speakers: list[str] = transcription.get("commentator_speakers", [])
    if commentator_speakers:
        utterances = [u for u in all_utterances if u["speaker"] in commentator_speakers]
        if not utterances:
            utterances = all_utterances  # graceful fallback
    else:
        utterances = all_utterances

    aligned: list[AlignedEvent] = []
    skipped_low_confidence = 0

    for ev_dict in raw_events:
        event = MatchEvent.from_dict(ev_dict)

        if event.event_type == EventType.SUBSTITUTION:
            continue

        estimated = estimate_video_timestamp(event, kickoff_first, kickoff_second)

        # Use excitement scoring for high-priority events so we snap to the
        # most excited nearby utterance, not just the temporally closest one.
        use_energy = event.event_type in _HIGH_PRIORITY_TYPES
        energy_fn = _utterance_excitement_score if use_energy else None

        refined, confidence = refine_timestamp(estimated, utterances, energy_fn=energy_fn)

        # Clamp to video bounds.
        refined = min(refined, max(0.0, video_duration - 1.0))

        # Drop low-confidence non-critical events — they usually indicate the
        # estimate drifted far from any commentary, meaning the clip window
        # will likely miss the action.
        if confidence < _MIN_CONFIDENCE_DEFAULT and event.event_type not in _HIGH_PRIORITY_TYPES:
            skipped_low_confidence += 1
            log.debug(
                "Skipping low-confidence event: %s %s' (confidence=%.1f)",
                event.event_type,
                event.minute,
                confidence,
            )
            continue

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

    log.info(
        "Aligned %d events for %s (%d low-confidence non-critical events dropped)",
        len(aligned),
        video_id,
        skipped_low_confidence,
    )

    result: dict[str, Any] = {
        "video_id": video_id,
        "workspace": str(workspace),
        "event_count": len(aligned),
        "events": [a.to_dict() for a in aligned],
    }

    cache_path.write_text(json.dumps(result, indent=2))
    log.info("Stage 4 complete — saved to %s", cache_path)
    return result
