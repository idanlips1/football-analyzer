"""Shared pytest fixtures for the test suite.

conftest.py is pytest's mechanism for sharing fixtures across test files.
The key fixture here (tmp_workspace) ensures tests never write into the real
pipeline_workspace/ directory — each test gets its own isolated temp folder.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect PIPELINE_WORKSPACE to a temporary directory for test isolation."""
    workspace = tmp_path / "pipeline_workspace"
    workspace.mkdir()
    monkeypatch.setattr("config.settings.PIPELINE_WORKSPACE", workspace)
    monkeypatch.setattr("pipeline.ingestion.PIPELINE_WORKSPACE", workspace)
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
