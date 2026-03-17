"""Tests for Stage 2 — transcription and commentator identification."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from pipeline.transcription import (
    identify_commentators,
    transcribe,
)

# ── identify_commentators ──────────────────────────────────────────────────


class TestIdentifyCommentators:
    def test_single_dominant_speaker(self) -> None:
        utterances = [
            {"speaker": "A", "start": 0, "end": 6000},
            {"speaker": "B", "start": 6000, "end": 7000},
            {"speaker": "C", "start": 7000, "end": 7500},
        ]
        # A has 6s, B has 1s (16%), C has 0.5s (8%) — only A qualifies at 30%
        assert identify_commentators(utterances) == ["A"]

    def test_two_commentators(self) -> None:
        utterances = [
            {"speaker": "A", "start": 0, "end": 5000},
            {"speaker": "B", "start": 5000, "end": 8000},
            {"speaker": "A", "start": 8000, "end": 13000},
            {"speaker": "B", "start": 13000, "end": 15000},
        ]
        # A = 10s, B = 5s (50% of A) — both qualify at default 30%
        assert identify_commentators(utterances) == ["A", "B"]

    def test_three_speakers_two_commentators(self) -> None:
        utterances = [
            {"speaker": "A", "start": 0, "end": 10000},
            {"speaker": "B", "start": 10000, "end": 16000},
            {"speaker": "C", "start": 16000, "end": 17000},
        ]
        # A = 10s, B = 6s (60%), C = 1s (10%) — A and B qualify
        assert identify_commentators(utterances) == ["A", "B"]

    def test_empty_utterances(self) -> None:
        assert identify_commentators([]) == []

    def test_single_speaker(self) -> None:
        utterances = [
            {"speaker": "X", "start": 0, "end": 5000},
            {"speaker": "X", "start": 6000, "end": 9000},
        ]
        assert identify_commentators(utterances) == ["X"]

    def test_custom_ratio_stricter(self) -> None:
        utterances = [
            {"speaker": "A", "start": 0, "end": 10000},
            {"speaker": "B", "start": 10000, "end": 14000},
        ]
        # B = 4s = 40% of A's 10s. With ratio=0.5, B doesn't qualify.
        assert identify_commentators(utterances, time_ratio=0.5) == ["A"]

    def test_custom_ratio_looser(self) -> None:
        utterances = [
            {"speaker": "A", "start": 0, "end": 10000},
            {"speaker": "B", "start": 10000, "end": 12000},
            {"speaker": "C", "start": 12000, "end": 13000},
        ]
        # A=10s, B=2s (20%), C=1s (10%). With ratio=0.1, all qualify.
        assert identify_commentators(utterances, time_ratio=0.1) == ["A", "B", "C"]

    def test_result_is_sorted(self) -> None:
        utterances = [
            {"speaker": "C", "start": 0, "end": 5000},
            {"speaker": "A", "start": 5000, "end": 10000},
        ]
        assert identify_commentators(utterances) == ["A", "C"]


# ── transcribe (integration with mocked AssemblyAI) ────────────────────────


class TestTranscribe:
    @staticmethod
    def _make_metadata(workspace: Path) -> dict:
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "match.mp4").write_bytes(b"\x00" * 512)
        return {
            "video_id": "test1",
            "video_filename": "match.mp4",
            "workspace": str(workspace),
        }

    @staticmethod
    def _fake_utterances() -> list[dict]:
        return [
            {"speaker": "A", "text": "What a goal!", "start": 0, "end": 5000},
            {"speaker": "B", "text": "Incredible.", "start": 5000, "end": 8000},
            {"speaker": "A", "text": "He scores!", "start": 8000, "end": 12000},
        ]

    def test_full_flow_saves_transcription(self, tmp_path: Path) -> None:
        ws = tmp_path / "pipeline_workspace" / "test1"
        metadata = self._make_metadata(ws)
        fake_utts = self._fake_utterances()

        with (
            patch("pipeline.transcription.extract_audio"),
            patch("pipeline.transcription._call_assemblyai", return_value=fake_utts),
        ):
            result = transcribe(metadata)

        assert result["total_utterances"] == 3
        assert result["commentator_speakers"] == ["A", "B"]
        assert (ws / "transcription.json").exists()

    def test_cache_hit_skips_processing(self, tmp_path: Path) -> None:
        ws = tmp_path / "pipeline_workspace" / "test2"
        metadata = self._make_metadata(ws)
        fake_utts = self._fake_utterances()

        with (
            patch("pipeline.transcription.extract_audio"),
            patch("pipeline.transcription._call_assemblyai", return_value=fake_utts) as mock_api,
        ):
            first = transcribe(metadata)
            second = transcribe(metadata)

        assert first == second
        assert mock_api.call_count == 1

    def test_skips_audio_extraction_if_file_exists(self, tmp_path: Path) -> None:
        ws = tmp_path / "pipeline_workspace" / "test3"
        metadata = self._make_metadata(ws)
        (ws / "audio.wav").write_bytes(b"\x00" * 256)

        with (
            patch("pipeline.transcription.extract_audio") as mock_extract,
            patch(
                "pipeline.transcription._call_assemblyai",
                return_value=self._fake_utterances(),
            ),
        ):
            transcribe(metadata)

        mock_extract.assert_not_called()

    def test_transcription_json_is_valid(self, tmp_path: Path) -> None:
        ws = tmp_path / "pipeline_workspace" / "test4"
        metadata = self._make_metadata(ws)

        with (
            patch("pipeline.transcription.extract_audio"),
            patch(
                "pipeline.transcription._call_assemblyai",
                return_value=self._fake_utterances(),
            ),
        ):
            result = transcribe(metadata)

        raw = json.loads((ws / "transcription.json").read_text())
        assert raw["utterances"] == result["utterances"]
        assert raw["commentator_speakers"] == ["A", "B"]
