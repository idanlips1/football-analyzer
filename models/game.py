"""GameState data model for a preprocessed match."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any


@dataclass
class GameState:
    video_id: str
    home_team: str
    away_team: str
    league: str
    date: str  # "YYYY-MM-DD"
    fixture_id: int | None
    video_filename: str  # filename only, e.g. "match.mp4"
    source: str  # canonical "https://www.youtube.com/watch?v=<id>"
    duration_seconds: float
    kickoff_first_half: float  # seconds in video — hand-confirmed during ingest
    kickoff_second_half: float  # seconds in video — hand-confirmed during ingest

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GameState:
        valid_keys = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in valid_keys})
