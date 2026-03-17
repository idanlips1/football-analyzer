"""Tests for Stage 1 — video ingestion and metadata validation."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.ingestion import (
    IngestionError,
    ingest,
    validate_duration,
)

# ── Duration validation ────────────────────────────────────────────────────


class TestValidateDuration:
    def test_rejects_short_video(self) -> None:
        with pytest.raises(IngestionError, match="only 300s"):
            validate_duration(300.0)

    def test_accepts_90_minute_match(self) -> None:
        validate_duration(5400.0)

    def test_skip_check_allows_short_video(self) -> None:
        validate_duration(60.0, skip_check=True)

    def test_exact_boundary_passes(self) -> None:
        validate_duration(20 * 60)

    def test_custom_minimum(self) -> None:
        with pytest.raises(IngestionError):
            validate_duration(100.0, min_duration=200.0)
        validate_duration(200.0, min_duration=200.0)


# ── Full ingestion flow ────────────────────────────────────────────────────


class TestIngest:
    """Tests for the ingest() function.

    We mock _extract_video_id and _download_video so these tests don't
    hit the network — they verify the orchestration logic (caching,
    duration gating, metadata persistence).
    """

    @staticmethod
    def _fake_download(workspace: Path) -> Path:
        """Simulate a downloaded file landing in the workspace."""
        video = workspace / "fake_match.mp4"
        video.write_bytes(b"\x00" * 512)
        return video

    @patch("pipeline.ingestion._extract_video_id", return_value="abc123")
    def test_saves_metadata_and_video(
        self,
        _mock_id: MagicMock,
        tmp_workspace: Path,
        fake_ffprobe_duration: Callable[[float], None],
    ) -> None:
        fake_ffprobe_duration(5400.0)
        ws = tmp_workspace / "abc123"

        with patch(
            "pipeline.ingestion._download_video",
            side_effect=lambda _url, w: self._fake_download(w),
        ):
            metadata = ingest("https://www.youtube.com/watch?v=abc123")

        assert metadata["video_id"] == "abc123"
        assert metadata["duration_seconds"] == 5400.0
        assert (ws / "metadata.json").exists()
        assert (ws / "fake_match.mp4").exists()

    @patch("pipeline.ingestion._extract_video_id", return_value="abc123")
    def test_cache_hit_skips_download(
        self,
        _mock_id: MagicMock,
        tmp_workspace: Path,
        fake_ffprobe_duration: Callable[[float], None],
    ) -> None:
        fake_ffprobe_duration(5400.0)

        with patch(
            "pipeline.ingestion._download_video",
            side_effect=lambda _url, w: self._fake_download(w),
        ) as mock_dl:
            first = ingest("https://www.youtube.com/watch?v=abc123")
            second = ingest("https://www.youtube.com/watch?v=abc123")

        assert first == second
        assert mock_dl.call_count == 1  # only downloaded once

    @patch("pipeline.ingestion._extract_video_id", return_value="short1")
    def test_rejects_short_video(
        self,
        _mock_id: MagicMock,
        tmp_workspace: Path,
        fake_ffprobe_duration: Callable[[float], None],
    ) -> None:
        fake_ffprobe_duration(300.0)

        with (
            patch(
                "pipeline.ingestion._download_video",
                side_effect=lambda _url, w: self._fake_download(w),
            ),
            pytest.raises(IngestionError, match="only 300s"),
        ):
            ingest("https://www.youtube.com/watch?v=short1")

    @patch("pipeline.ingestion._extract_video_id", return_value="short2")
    def test_skip_flag_allows_short_video(
        self,
        _mock_id: MagicMock,
        tmp_workspace: Path,
        fake_ffprobe_duration: Callable[[float], None],
    ) -> None:
        fake_ffprobe_duration(300.0)

        with patch(
            "pipeline.ingestion._download_video",
            side_effect=lambda _url, w: self._fake_download(w),
        ):
            metadata = ingest(
                "https://www.youtube.com/watch?v=short2",
                skip_duration_check=True,
            )

        assert metadata["duration_seconds"] == 300.0

    @patch("pipeline.ingestion._extract_video_id", return_value="meta1")
    def test_metadata_json_is_valid(
        self,
        _mock_id: MagicMock,
        tmp_workspace: Path,
        fake_ffprobe_duration: Callable[[float], None],
    ) -> None:
        fake_ffprobe_duration(5400.0)

        with patch(
            "pipeline.ingestion._download_video",
            side_effect=lambda _url, w: self._fake_download(w),
        ):
            metadata = ingest("https://www.youtube.com/watch?v=meta1")

        raw = json.loads(Path(metadata["workspace"], "metadata.json").read_text())
        assert raw["video_id"] == "meta1"
        assert raw["source"] == "https://www.youtube.com/watch?v=meta1"
        assert raw["duration_seconds"] == 5400.0
