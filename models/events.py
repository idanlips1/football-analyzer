"""Data models for EDR entries, event types, and pipeline metadata."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    GOAL = "goal"
    SHOT_ON_TARGET = "shot_on_target"
    SAVE = "save"
    FOUL = "foul"
    CARD = "card"
    CORNER = "corner"
    FREE_KICK = "free_kick"
    COUNTER_ATTACK = "counter_attack"
    CELEBRATION = "celebration"
    PENALTY = "penalty"
    VAR_REVIEW = "var_review"
    OTHER = "other"


@dataclass
class EDREntry:
    timestamp_start: float  # seconds (utterance start ms / 1000)
    timestamp_end: float  # seconds (utterance end ms / 1000)
    commentator_energy: float  # RMS energy, normalized 0.0–1.0
    commentator_text: str  # raw utterance text
    keyword_matches: list[str]  # matched keyword strings
    event_type: EventType
    llm_description: str  # one-sentence description from LLM
    llm_excitement_score: float  # 0.0–10.0 from LLM
    final_score: float  # weighted combination 0.0–10.0
    include_in_highlights: bool  # True if final_score >= EXCITEMENT_THRESHOLD

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["event_type"] = self.event_type.value  # enum → str for JSON
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EDREntry:
        data = dict(data)
        data["event_type"] = EventType(data["event_type"])
        return cls(**data)


@dataclass
class VideoMetadata:
    video_id: str
    duration: float  # seconds
    resolution: str  # e.g. "1920x1080"
    fps: float
    path: str  # absolute path to video file
