"""Tests for Stage 1 — local catalog video copy and metadata validation."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from pipeline.ingestion import (
    IngestionError,
    ingest_local_catalog_match,
    validate_duration,
)
from utils.storage import LocalStorage

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


# ── ingest_local_catalog_match ─────────────────────────────────────────────


class TestIngestLocalCatalogMatch:
    """Copy + metadata; ffprobe mocked."""

    @staticmethod
    def _src_mp4(tmp_path: Path) -> Path:
        p = tmp_path / "in.mp4"
        p.write_bytes(b"\x00" * 512)
        return p

    def test_saves_metadata_and_video(
        self,
        tmp_path: Path,
        fake_ffprobe_duration: Callable[[float], None],
    ) -> None:
        fake_ffprobe_duration(5400.0)
        storage = LocalStorage(root=tmp_path / "ws")
        src = self._src_mp4(tmp_path)

        meta = ingest_local_catalog_match("istanbul-2005", src, storage)

        assert meta["video_id"] == "istanbul-2005"
        assert meta["duration_seconds"] == 5400.0
        assert storage.local_path("istanbul-2005", "metadata.json").exists()
        assert storage.local_path("istanbul-2005", "match.mp4").exists()

    def test_empty_match_id_rejected(self, tmp_path: Path) -> None:
        storage = LocalStorage(root=tmp_path / "ws")
        src = self._src_mp4(tmp_path)
        with pytest.raises(IngestionError, match="match_id is empty"):
            ingest_local_catalog_match("   ", src, storage)

    def test_rejects_short_video(
        self,
        tmp_path: Path,
        fake_ffprobe_duration: Callable[[float], None],
    ) -> None:
        fake_ffprobe_duration(300.0)
        storage = LocalStorage(root=tmp_path / "ws")
        src = self._src_mp4(tmp_path)

        with pytest.raises(IngestionError, match="only 300s"):
            ingest_local_catalog_match("istanbul-2005", src, storage)

    def test_skip_flag_allows_short_video(
        self,
        tmp_path: Path,
        fake_ffprobe_duration: Callable[[float], None],
    ) -> None:
        fake_ffprobe_duration(300.0)
        storage = LocalStorage(root=tmp_path / "ws")
        src = self._src_mp4(tmp_path)

        meta = ingest_local_catalog_match(
            "istanbul-2005",
            src,
            storage,
            skip_duration_check=True,
        )

        assert meta["duration_seconds"] == 300.0

    def test_metadata_json_is_valid(
        self,
        tmp_path: Path,
        fake_ffprobe_duration: Callable[[float], None],
    ) -> None:
        fake_ffprobe_duration(5400.0)
        storage = LocalStorage(root=tmp_path / "ws")
        src = self._src_mp4(tmp_path)

        ingest_local_catalog_match("istanbul-2005", src, storage)

        raw = json.loads(storage.local_path("istanbul-2005", "metadata.json").read_text())
        assert raw["video_id"] == "istanbul-2005"
        assert raw["source"] == "catalog:istanbul-2005"
        assert raw["duration_seconds"] == 5400.0
