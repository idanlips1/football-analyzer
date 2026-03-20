"""Per-event-type clip window durations and priority ordering.

Pre-roll = seconds before the event to include (build-up play).
Post-roll = seconds after the event to include (celebration, replay, aftermath).
"""

from models.events import EventType

# (pre_roll_seconds, post_roll_seconds)
CLIP_WINDOWS: dict[EventType, tuple[float, float]] = {
    EventType.GOAL: (15.0, 30.0),
    EventType.OWN_GOAL: (10.0, 25.0),
    EventType.PENALTY: (10.0, 25.0),
    EventType.MISSED_PENALTY: (10.0, 20.0),
    EventType.RED_CARD: (10.0, 15.0),
    EventType.YELLOW_CARD: (5.0, 10.0),
    EventType.NEAR_MISS: (10.0, 15.0),
    EventType.SAVE: (10.0, 15.0),
    EventType.SHOT_ON_TARGET: (10.0, 15.0),
    EventType.VAR_REVIEW: (5.0, 20.0),
}

DEFAULT_WINDOW: tuple[float, float] = (10.0, 15.0)

# Highest priority first — used by budget enforcement to decide which clips to
# drop when total duration exceeds the highlights budget.
EVENT_PRIORITY: list[EventType] = [
    EventType.GOAL,
    EventType.OWN_GOAL,
    EventType.PENALTY,
    EventType.MISSED_PENALTY,
    EventType.RED_CARD,
    EventType.VAR_REVIEW,
    EventType.NEAR_MISS,
    EventType.SAVE,
    EventType.SHOT_ON_TARGET,
    EventType.YELLOW_CARD,
]


def get_window(event_type: EventType) -> tuple[float, float]:
    """Return (pre_roll, post_roll) for the given event type."""
    return CLIP_WINDOWS.get(event_type, DEFAULT_WINDOW)


def get_priority(event_type: EventType) -> int:
    """Lower number = higher priority. Unlisted types get lowest priority."""
    try:
        return EVENT_PRIORITY.index(event_type)
    except ValueError:
        return len(EVENT_PRIORITY)
