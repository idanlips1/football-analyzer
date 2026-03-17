"""OpenAI json_schema strict mode definition for batch utterance classification."""

from __future__ import annotations

from models.events import EventType

# All valid event_type values — kept in sync with EventType enum automatically.
_EVENT_TYPE_VALUES: list[str] = [e.value for e in EventType]

BATCH_RESPONSE_SCHEMA: dict[str, object] = {
    "name": "batch_classification",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "classifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "event_type": {"enum": _EVENT_TYPE_VALUES},
                        "description": {"type": "string"},
                        "excitement_score": {"type": "number"},
                    },
                    "required": ["index", "event_type", "description", "excitement_score"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["classifications"],
        "additionalProperties": False,
    },
}
