"""Data models for EDR entries, event types, and pipeline metadata."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


def seconds_to_timestamp(seconds: float) -> str:
    """Convert fractional seconds to ``HH:MM:SS`` string."""
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def timestamp_to_seconds(ts: str) -> float:
    """Convert ``HH:MM:SS`` string back to fractional seconds."""
    parts = ts.split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])


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
    MISSED_PENALTY = "missed_penalty"
    VAR_REVIEW = "var_review"
    NEAR_MISS = "near_miss"
    RED_CARD = "red_card"
    YELLOW_CARD = "yellow_card"
    OWN_GOAL = "own_goal"
    SUBSTITUTION = "substitution"
    UNKNOWN = "unknown"
    OTHER = "other"


@dataclass
class ExcitementEntry:
    """Per-utterance analysis output from Stage 3."""

    timestamp_start: float  # seconds (utterance start ms / 1000)
    timestamp_end: float  # seconds (utterance end ms / 1000)
    commentator_energy: float  # normalized energy (0 = baseline, >0 = above average)
    commentator_text: str  # raw utterance text
    keyword_matches: list[str]  # matched keyword strings
    event_type: EventType
    llm_description: str  # one-sentence description from LLM
    llm_excitement_score: float  # 0.0–10.0 from LLM
    final_score: float  # weighted combination 0.0–10.0
    include_in_highlights: bool  # True if final_score >= EXCITEMENT_THRESHOLD

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["event_type"] = self.event_type.value
        d["timestamp_start"] = seconds_to_timestamp(self.timestamp_start)
        d["timestamp_end"] = seconds_to_timestamp(self.timestamp_end)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExcitementEntry:
        data = dict(data)
        data["event_type"] = EventType(data["event_type"])
        if isinstance(data["timestamp_start"], str):
            data["timestamp_start"] = timestamp_to_seconds(data["timestamp_start"])
        if isinstance(data["timestamp_end"], str):
            data["timestamp_end"] = timestamp_to_seconds(data["timestamp_end"])
        return cls(**data)


@dataclass
class EDREntry:
    """Merged clip entry produced by Stage 4."""

    start_seconds: float  # clip start in video
    end_seconds: float  # clip end in video
    score: float  # composite excitement score 0.0–1.0
    event_type: EventType  # event classification
    keyword_hits: list[str]  # keywords that triggered in this window
    energy_peak: float  # peak vocal energy value
    video_id: str  # workspace linkage

    @property
    def duration(self) -> float:
        return self.end_seconds - self.start_seconds

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["event_type"] = self.event_type.value
        d["start_seconds"] = seconds_to_timestamp(self.start_seconds)
        d["end_seconds"] = seconds_to_timestamp(self.end_seconds)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EDREntry:
        data = dict(data)
        data["event_type"] = EventType(data["event_type"])
        if isinstance(data["start_seconds"], str):
            data["start_seconds"] = timestamp_to_seconds(data["start_seconds"])
        if isinstance(data["end_seconds"], str):
            data["end_seconds"] = timestamp_to_seconds(data["end_seconds"])
        return cls(**data)


@dataclass
class MatchEvent:
    """Single match event from the API-Football data source."""

    minute: int
    extra_minute: int | None  # stoppage time (e.g. 3 for 90+3')
    half: str  # "1st Half" / "2nd Half" / "Extra Time"
    event_type: EventType
    team: str
    player: str
    assist: str | None
    score: str  # running score at time of event, e.g. "2 - 1"
    detail: str  # "Normal Goal", "Penalty", "Own Goal", "yellow card", etc.

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["event_type"] = self.event_type.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MatchEvent:
        data = dict(data)
        data["event_type"] = EventType(data["event_type"])
        return cls(**data)


@dataclass
class AlignedEvent:
    """Match event aligned to a video timestamp (output of Stage 4)."""

    event_type: EventType
    minute: int
    extra_minute: int | None
    half: str
    player: str
    team: str
    score: str
    detail: str
    estimated_video_ts: float  # seconds — from kickoff offset formula
    refined_video_ts: float  # seconds — after audio energy refinement
    confidence: float  # 0.0–1.0, how well the audio corroborated the estimate

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["event_type"] = self.event_type.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AlignedEvent:
        data = dict(data)
        data["event_type"] = EventType(data["event_type"])
        return cls(**data)

    @property
    def display_time(self) -> str:
        """Human-readable match time, e.g. ``83'`` or ``90+3'``."""
        if self.extra_minute:
            return f"{self.minute}+{self.extra_minute}'"
        return f"{self.minute}'"


@dataclass
class VideoMetadata:
    video_id: str
    duration: float  # seconds
    resolution: str  # e.g. "1920x1080"
    fps: float
    path: str  # absolute path to video file
