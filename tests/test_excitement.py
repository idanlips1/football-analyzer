"""Tests for Stage 3 — vocal energy analysis and keyword detection."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import openai
import pytest

from config.keywords import KEYWORD_WEIGHTS
from config.llm_schema import BATCH_RESPONSE_SCHEMA
from models.events import EventType
from pipeline.excitement import (
    ExcitementError,
    _classify_batch_with_llm,
    _compute_baseline_energy,
    _compute_energy,
    _format_match_time,
    _keyword_score,
    _match_keywords,
    _normalize_energy,
    analyze_excitement,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_utterance(i: int, speaker: str = "A", text: str | None = None) -> dict[str, Any]:
    return {
        "speaker": speaker,
        "text": text or f"utterance {i}",
        "start": i * 5000,
        "end": (i + 1) * 5000,
    }


def _make_batch(n: int) -> list[dict[str, Any]]:
    return [
        {
            "utterance": _make_utterance(i),
            "energy": 0.5,
            "keywords": [],
        }
        for i in range(n)
    ]


def _fake_transcription() -> dict[str, Any]:
    """Utterances with speakers A, B (commentators) and C (not commentator)."""
    return {
        "commentator_speakers": ["A", "B"],
        "utterances": [
            {"speaker": "A", "text": "What a goal! Incredible!", "start": 0, "end": 3000},
            {"speaker": "B", "text": "He scores!", "start": 3000, "end": 5000},
            {"speaker": "C", "text": "This is an interview subject", "start": 5000, "end": 7000},
        ],
    }


def _fake_metadata(workspace: Path) -> dict[str, Any]:
    return {"workspace": str(workspace), "video_filename": "video.mp4"}


def _write_fake_audio(workspace: Path) -> Path:
    audio = workspace / "audio.wav"
    audio.write_bytes(b"\x00" * 512)
    return audio


def _make_llm_response(classifications: list[dict[str, Any]]) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = json.dumps({"classifications": classifications})
    return mock_resp


def _good_llm_response_for(n: int) -> MagicMock:
    return _make_llm_response(
        [
            {"index": i, "event_type": "goal", "description": "A goal", "excitement_score": 9.0}
            for i in range(n)
        ]
    )


def _patch_librosa() -> tuple[Any, Any]:
    """Return two patch context managers for librosa.load and librosa.feature.rms."""
    load_patch = patch(
        "pipeline.excitement.librosa.load",
        return_value=(np.zeros(1600), 16000),
    )
    rms_patch = patch(
        "pipeline.excitement.librosa.feature.rms",
        return_value=np.array([[0.3]]),
    )
    return load_patch, rms_patch


@contextmanager
def _patch_azure_settings() -> Iterator[None]:
    """Patch all Azure OpenAI settings to test values."""
    with (
        patch("pipeline.excitement.AZURE_OPENAI_API_KEY", "test-key"),
        patch("pipeline.excitement.AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com"),
        patch("pipeline.excitement.AZURE_OPENAI_DEPLOYMENT", "test-deployment"),
    ):
        yield


# ── TestKeywordMatching ───────────────────────────────────────────────────────


class TestKeywordMatching:
    def test_single_keyword_matched(self) -> None:
        assert "goal" in _match_keywords("The player scored a goal")

    def test_multi_word_keyword_matched(self) -> None:
        assert "free kick" in _match_keywords("A free kick was awarded")

    def test_case_insensitive_matching(self) -> None:
        assert "goal" in _match_keywords("GOAL of the season!")

    def test_no_keywords_matched(self) -> None:
        assert _match_keywords("The ball is in play now") == []

    def test_multiple_keywords_matched(self) -> None:
        matched = _match_keywords("What an incredible goal!")
        assert "goal" in matched
        assert "incredible" in matched

    def test_keyword_score_single(self) -> None:
        score = _keyword_score(["goal"])
        assert score == KEYWORD_WEIGHTS["goal"]

    def test_keyword_score_capped_at_one(self) -> None:
        score = _keyword_score(["goal", "incredible", "unbelievable"])
        assert score == 1.0


# ── TestComputeEnergy ─────────────────────────────────────────────────────────


class TestComputeEnergy:
    def test_normal_segment_returns_float(self, tmp_path: Path) -> None:
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"\x00" * 512)
        with (
            patch("pipeline.excitement.librosa.load", return_value=(np.zeros(1600), 16000)),
            patch(
                "pipeline.excitement.librosa.feature.rms",
                return_value=np.array([[0.05]]),
            ),
        ):
            result = _compute_energy(audio, 0.0, 1.0)
        assert isinstance(result, float)
        assert result >= 0.0

    def test_silent_segment_returns_low(self, tmp_path: Path) -> None:
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"\x00" * 512)
        with (
            patch("pipeline.excitement.librosa.load", return_value=(np.zeros(1600), 16000)),
            patch(
                "pipeline.excitement.librosa.feature.rms",
                return_value=np.array([[0.001]]),
            ),
        ):
            result = _compute_energy(audio, 0.0, 1.0)
        assert result < 0.1

    def test_zero_duration_returns_zero(self, tmp_path: Path) -> None:
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"\x00" * 512)
        with patch("pipeline.excitement.librosa.load") as mock_load:
            result = _compute_energy(audio, 0.0, 0.0)
        assert result == 0.0
        mock_load.assert_not_called()


# ── TestNormalizeEnergy ───────────────────────────────────────────────────────


class TestNormalizeEnergy:
    def test_average_energy_yields_half(self) -> None:
        result = _normalize_energy(0.04, 0.04)
        assert result == pytest.approx(0.5)

    def test_double_baseline_yields_one(self) -> None:
        result = _normalize_energy(0.08, 0.04)
        assert result == pytest.approx(1.0)

    def test_capped_at_one(self) -> None:
        result = _normalize_energy(0.20, 0.04)
        assert result == 1.0

    def test_zero_baseline_returns_zero(self) -> None:
        assert _normalize_energy(0.05, 0.0) == 0.0


# ── TestBaselineEnergy ────────────────────────────────────────────────────────


class TestBaselineEnergy:
    def test_returns_mean_of_utterance_energies(self, tmp_path: Path) -> None:
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"\x00" * 512)
        utts = [
            {"start": 0, "end": 1000},
            {"start": 1000, "end": 2000},
        ]
        energies = iter([0.02, 0.06])
        with patch(
            "pipeline.excitement._compute_energy",
            side_effect=lambda *_a, **_k: next(energies),
        ):
            result = _compute_baseline_energy(audio, utts)
        assert result == pytest.approx(0.04)

    def test_empty_utterances_returns_fallback(self, tmp_path: Path) -> None:
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"\x00" * 512)
        assert _compute_baseline_energy(audio, []) == pytest.approx(0.01)


# ── TestFormatMatchTime ──────────────────────────────────────────────────────


class TestFormatMatchTime:
    def test_formats_correctly(self) -> None:
        utt = {"start": 2109050, "end": 2128770}
        assert _format_match_time(utt) == "35:09"

    def test_zero(self) -> None:
        assert _format_match_time({"start": 0, "end": 1000}) == "0:00"


# ── TestClassifyBatchWithLlm ──────────────────────────────────────────────────


class TestClassifyBatchWithLlm:
    def test_all_indices_returned(self) -> None:
        batch = _make_batch(2)
        mock_resp = _make_llm_response(
            [
                {
                    "index": 0,
                    "event_type": "goal",
                    "description": "A goal!",
                    "excitement_score": 9.0,
                },
                {
                    "index": 1,
                    "event_type": "other",
                    "description": "Nothing",
                    "excitement_score": 1.0,
                },
            ]
        )
        with patch("pipeline.excitement.openai.AzureOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = mock_resp
            with _patch_azure_settings():
                result = _classify_batch_with_llm(batch)
        assert set(result.keys()) == {0, 1}
        assert result[0]["event_type"] == "goal"
        assert result[0]["excitement_score"] == 9.0

    def test_missing_index_retried_once(self) -> None:
        batch = _make_batch(2)
        first_resp = _make_llm_response(
            [{"index": 0, "event_type": "goal", "description": "A goal!", "excitement_score": 9.0}]
        )
        second_resp = _make_llm_response(
            [
                {
                    "index": 0,
                    "event_type": "goal",
                    "description": "A goal!",
                    "excitement_score": 9.0,
                },
                {
                    "index": 1,
                    "event_type": "shot_on_target",
                    "description": "A shot",
                    "excitement_score": 6.0,
                },
            ]
        )
        mock_create = MagicMock(side_effect=[first_resp, second_resp])
        with patch("pipeline.excitement.openai.AzureOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create = mock_create
            with _patch_azure_settings():
                result = _classify_batch_with_llm(batch)
        assert mock_create.call_count == 2
        assert 1 in result
        assert result[1]["excitement_score"] == 6.0

    def test_missing_index_after_retry_uses_default(self) -> None:
        batch = _make_batch(2)
        partial_resp = _make_llm_response(
            [{"index": 0, "event_type": "goal", "description": "A goal!", "excitement_score": 9.0}]
        )
        mock_create = MagicMock(return_value=partial_resp)
        with patch("pipeline.excitement.openai.AzureOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create = mock_create
            with _patch_azure_settings(), patch("pipeline.excitement.log") as mock_log:
                result = _classify_batch_with_llm(batch)
        assert 1 in result
        assert result[1]["excitement_score"] == 0.0
        assert mock_log.warning.called

    def test_api_error_raises_excitement_error(self) -> None:
        batch = _make_batch(1)
        with patch("pipeline.excitement.openai.AzureOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.side_effect = openai.OpenAIError(
                "timeout"
            )
            with _patch_azure_settings(), pytest.raises(ExcitementError):
                _classify_batch_with_llm(batch)

    def test_unknown_event_type_falls_back_to_other(self) -> None:
        batch = _make_batch(1)
        mock_resp = _make_llm_response(
            [
                {
                    "index": 0,
                    "event_type": "totally_unknown_type",
                    "description": "???",
                    "excitement_score": 5.0,
                }
            ]
        )
        with patch("pipeline.excitement.openai.AzureOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = mock_resp
            with _patch_azure_settings():
                result = _classify_batch_with_llm(batch)
        assert result[0]["event_type"] == EventType.OTHER.value

    def test_score_clamped_to_range(self) -> None:
        batch = _make_batch(1)
        mock_resp = _make_llm_response(
            [
                {
                    "index": 0,
                    "event_type": "goal",
                    "description": "Amazing!",
                    "excitement_score": 11.0,
                }
            ]
        )
        with patch("pipeline.excitement.openai.AzureOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = mock_resp
            with _patch_azure_settings():
                result = _classify_batch_with_llm(batch)
        assert result[0]["excitement_score"] == 10.0

    def test_missing_api_key_raises(self) -> None:
        batch = _make_batch(1)
        with (
            patch("pipeline.excitement.AZURE_OPENAI_API_KEY", ""),
            patch("pipeline.excitement.OPENAI_API_KEY", ""),
            pytest.raises(ExcitementError, match="AZURE_OPENAI_API_KEY"),
        ):
            _classify_batch_with_llm(batch)


# ── TestAnalyzeExcitement ─────────────────────────────────────────────────────


class TestAnalyzeExcitement:
    def test_cache_hit_skips_processing(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        cached = [{"event_type": "goal", "final_score": 9.0}]
        (workspace / "excitement.json").write_text(json.dumps(cached))
        meta = _fake_metadata(workspace)
        with patch("pipeline.excitement.librosa.load") as mock_load:
            result = analyze_excitement(_fake_transcription(), meta)
        assert result == cached
        mock_load.assert_not_called()

    def test_saves_excitement_json(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _write_fake_audio(workspace)
        transcript = _fake_transcription()
        n_commentator = sum(
            1
            for u in transcript["utterances"]
            if u["speaker"] in transcript["commentator_speakers"]
        )
        load_p, rms_p = _patch_librosa()
        with load_p, rms_p, patch("pipeline.excitement.openai.AzureOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _good_llm_response_for(
                n_commentator
            )
            with _patch_azure_settings():
                analyze_excitement(transcript, _fake_metadata(workspace))
        assert (workspace / "excitement.json").exists()

    def test_output_is_list_of_dicts(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _write_fake_audio(workspace)
        transcript = _fake_transcription()
        n_commentator = sum(
            1
            for u in transcript["utterances"]
            if u["speaker"] in transcript["commentator_speakers"]
        )
        load_p, rms_p = _patch_librosa()
        with load_p, rms_p, patch("pipeline.excitement.openai.AzureOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _good_llm_response_for(
                n_commentator
            )
            with _patch_azure_settings():
                result = analyze_excitement(transcript, _fake_metadata(workspace))
        assert isinstance(result, list)
        assert all(isinstance(item, dict) for item in result)

    def test_output_json_structure_valid(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _write_fake_audio(workspace)
        transcript = _fake_transcription()
        n_commentator = sum(
            1
            for u in transcript["utterances"]
            if u["speaker"] in transcript["commentator_speakers"]
        )
        load_p, rms_p = _patch_librosa()
        with load_p, rms_p, patch("pipeline.excitement.openai.AzureOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _good_llm_response_for(
                n_commentator
            )
            with _patch_azure_settings():
                result = analyze_excitement(transcript, _fake_metadata(workspace))
        required_keys = {
            "timestamp_start",
            "timestamp_end",
            "commentator_energy",
            "commentator_text",
            "keyword_matches",
            "event_type",
            "llm_description",
            "llm_excitement_score",
            "final_score",
            "include_in_highlights",
        }
        for entry in result:
            assert required_keys.issubset(entry.keys())

    def test_timestamps_are_hh_mm_ss(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _write_fake_audio(workspace)
        transcript: dict[str, Any] = {
            "commentator_speakers": ["A"],
            "utterances": [
                {"speaker": "A", "text": "Goal!", "start": 3661000, "end": 3665000},
            ],
        }
        load_p, rms_p = _patch_librosa()
        with load_p, rms_p, patch("pipeline.excitement.openai.AzureOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _good_llm_response_for(1)
            with _patch_azure_settings():
                result = analyze_excitement(transcript, _fake_metadata(workspace))
        assert result[0]["timestamp_start"] == "01:01:01"
        assert result[0]["timestamp_end"] == "01:01:05"

    def test_only_commentator_utterances_processed(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _write_fake_audio(workspace)
        transcript = _fake_transcription()
        load_p, rms_p = _patch_librosa()
        with load_p, rms_p, patch("pipeline.excitement.openai.AzureOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _good_llm_response_for(2)
            with _patch_azure_settings():
                result = analyze_excitement(transcript, _fake_metadata(workspace))
        assert len(result) == 2
        texts = [e["commentator_text"] for e in result]
        assert "This is an interview subject" not in texts

    def test_missing_audio_raises(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        with pytest.raises(ExcitementError, match="audio.wav not found"):
            analyze_excitement(_fake_transcription(), _fake_metadata(workspace))

    def test_empty_utterances_returns_empty_list(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _write_fake_audio(workspace)
        transcript: dict[str, Any] = {"commentator_speakers": ["A"], "utterances": []}
        load_p, rms_p = _patch_librosa()
        with load_p, rms_p:
            result = analyze_excitement(transcript, _fake_metadata(workspace))
        assert result == []

    def test_include_in_highlights_flag(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _write_fake_audio(workspace)
        transcript: dict[str, Any] = {
            "commentator_speakers": ["A"],
            "utterances": [{"speaker": "A", "text": "What a goal!", "start": 0, "end": 3000}],
        }
        mock_resp = _make_llm_response(
            [{"index": 0, "event_type": "goal", "description": "A goal", "excitement_score": 9.0}]
        )
        load_p, rms_p = _patch_librosa()
        with load_p, rms_p, patch("pipeline.excitement.openai.AzureOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = mock_resp
            with _patch_azure_settings():
                result = analyze_excitement(transcript, _fake_metadata(workspace))
        assert result[0]["include_in_highlights"] is True

    def test_chunks_across_batch_boundary(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _write_fake_audio(workspace)
        utterances = [
            {
                "speaker": "A",
                "text": f"utterance {i}",
                "start": i * 3000,
                "end": (i + 1) * 3000,
            }
            for i in range(21)
        ]
        transcript: dict[str, Any] = {"commentator_speakers": ["A"], "utterances": utterances}
        batch1_resp = _make_llm_response(
            [
                {"index": i, "event_type": "other", "description": "ok", "excitement_score": 1.0}
                for i in range(20)
            ]
        )
        batch2_resp = _make_llm_response(
            [{"index": 0, "event_type": "other", "description": "ok", "excitement_score": 1.0}]
        )
        mock_create = MagicMock(side_effect=[batch1_resp, batch2_resp])
        load_p, rms_p = _patch_librosa()
        with load_p, rms_p, patch("pipeline.excitement.openai.AzureOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create = mock_create
            with _patch_azure_settings():
                result = analyze_excitement(transcript, _fake_metadata(workspace))
        assert len(result) == 21
        assert mock_create.call_count == 2


# ── TestLlmSchema ─────────────────────────────────────────────────────────────


class TestLlmSchema:
    def test_batch_response_schema_has_required_structure(self) -> None:
        schema = BATCH_RESPONSE_SCHEMA
        assert schema["name"] == "batch_classification"
        assert schema["strict"] is True
        inner = schema["schema"]
        assert isinstance(inner, dict)
        assert "classifications" in inner["properties"]  # type: ignore[index]
        items = inner["properties"]["classifications"]["items"]  # type: ignore[index]
        required = items["required"]
        assert "index" in required
        assert "event_type" in required
        assert "description" in required
        assert "excitement_score" in required
        event_type_values = items["properties"]["event_type"]["enum"]
        for et in EventType:
            assert et.value in event_type_values
