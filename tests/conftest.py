"""Shared pytest fixtures for the test suite.

conftest.py is pytest's mechanism for sharing fixtures across test files.
The key fixture here (tmp_workspace) ensures tests never write into the real
pipeline_workspace/ directory — each test gets its own isolated temp folder.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from models.events import EventType, MatchEvent
from utils.storage import LocalStorage


@pytest.fixture()
def tmp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect PIPELINE_WORKSPACE to a temporary directory for test isolation."""
    workspace = tmp_path / "pipeline_workspace"
    workspace.mkdir()
    monkeypatch.setattr("config.settings.PIPELINE_WORKSPACE", workspace)
    monkeypatch.setattr("pipeline.ingestion.PIPELINE_WORKSPACE", workspace)
    monkeypatch.setattr("pipeline.edr.PIPELINE_WORKSPACE", workspace)
    monkeypatch.setattr("pipeline.filtering.PIPELINE_WORKSPACE", workspace)
    monkeypatch.setattr("pipeline.video.PIPELINE_WORKSPACE", workspace)
    monkeypatch.setattr("pipeline.match_finder.PIPELINE_WORKSPACE", workspace)
    monkeypatch.setattr("pipeline.event_aligner.PIPELINE_WORKSPACE", workspace)
    monkeypatch.setattr("pipeline.clip_builder.PIPELINE_WORKSPACE", workspace)
    return workspace


@pytest.fixture()
def fake_ffprobe_duration(monkeypatch: pytest.MonkeyPatch) -> Callable[[float], None]:
    """Patch get_video_duration so tests don't need real video files or ffprobe."""

    def _patch(duration_seconds: float) -> None:
        monkeypatch.setattr(
            "pipeline.ingestion.get_video_duration",
            lambda _path: duration_seconds,
        )

    return _patch


@pytest.fixture()
def tmp_storage(tmp_path: Path) -> LocalStorage:
    """LocalStorage backed by a temporary directory for test isolation."""
    root = tmp_path / "pipeline_workspace"
    root.mkdir()
    return LocalStorage(root=root)


@pytest.fixture()
def sample_match_events() -> list[MatchEvent]:
    """Liverpool 3-1 Man City Community Shield 2022 — key events."""
    return [
        MatchEvent(
            minute=21,
            extra_minute=None,
            half="1st Half",
            event_type=EventType.GOAL,
            team="Liverpool",
            player="Trent Alexander-Arnold",
            assist=None,
            score="1 - 0",
            detail="Normal Goal",
        ),
        MatchEvent(
            minute=42,
            extra_minute=None,
            half="1st Half",
            event_type=EventType.YELLOW_CARD,
            team="Manchester City",
            player="Ruben Dias",
            assist=None,
            score="1 - 0",
            detail="yellow card",
        ),
        MatchEvent(
            minute=70,
            extra_minute=None,
            half="2nd Half",
            event_type=EventType.GOAL,
            team="Manchester City",
            player="Julian Alvarez",
            assist=None,
            score="1 - 1",
            detail="Normal Goal",
        ),
        MatchEvent(
            minute=83,
            extra_minute=None,
            half="2nd Half",
            event_type=EventType.PENALTY,
            team="Liverpool",
            player="Mohamed Salah",
            assist=None,
            score="2 - 1",
            detail="Penalty",
        ),
        MatchEvent(
            minute=90,
            extra_minute=4,
            half="2nd Half",
            event_type=EventType.GOAL,
            team="Liverpool",
            player="Darwin Nunez",
            assist="Andrew Robertson",
            score="3 - 1",
            detail="Normal Goal",
        ),
    ]


@pytest.fixture()
def sample_transcription_with_kickoff() -> dict[str, Any]:
    """Transcription data with kickoff timestamps detected."""
    return {
        "audio_filename": "audio.wav",
        "total_utterances": 342,
        "commentator_speakers": ["A", "B"],
        "utterances": [
            {
                "speaker": "A",
                "text": "Welcome to the Community Shield",
                "start": 120_000,
                "end": 125_000,
            },
            {
                "speaker": "A",
                "text": "And we're underway here at the King Power Stadium",
                "start": 330_000,
                "end": 335_000,
            },
            {
                "speaker": "A",
                "text": "The second half is underway",
                "start": 3_420_000,
                "end": 3_423_000,
            },
        ],
        "kickoff_first_half": 330.0,
        "kickoff_second_half": 3420.0,
    }
