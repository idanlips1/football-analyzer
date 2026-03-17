"""Stage 3 — Commentator excitement analysis.

Vocal energy, keyword detection, and LLM classification.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import librosa
import numpy as np
import openai

from config.keywords import KEYWORD_WEIGHTS
from config.llm_schema import BATCH_RESPONSE_SCHEMA
from config.settings import (
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_DEPLOYMENT,
    AZURE_OPENAI_ENDPOINT,
    EXCITEMENT_BATCH_SIZE,
    EXCITEMENT_ENERGY_WEIGHT,
    EXCITEMENT_KEYWORD_WEIGHT,
    EXCITEMENT_LLM_WEIGHT,
    EXCITEMENT_THRESHOLD,
    OPENAI_API_KEY,
    OPENAI_MODEL,
)
from models.events import EventType, ExcitementEntry
from utils.logger import get_logger

log = get_logger(__name__)

AUDIO_FILENAME = "audio.wav"
EXCITEMENT_FILENAME = "excitement.json"


class ExcitementError(Exception):
    """Raised when excitement analysis fails."""


_LLM_SYSTEM_PROMPT = """\
You are an expert football match analyst building a highlights reel from live commentary.

Your job is to decide whether each commentator utterance describes a LIVE, REAL-TIME \
match event worth including in highlights.

Rules:
1. Only classify events happening RIGHT NOW in the match. If the commentator is \
recalling past matches, telling anecdotes, discussing player history, or speculating \
about the future, set excitement_score to 0 and event_type to "other".
2. Look for present-tense action language: "shoots!", "saves!", "goal!", "he's through!" \
vs past-tense storytelling: "he scored a hat trick last season", "back in 2019…".
3. A timestamp is provided for each utterance — use it to judge whether the commentator \
is describing live action at that point in the match.
4. excitement_score: 0=mundane/anecdote, 5=notable live event, 8=very exciting, \
10=legendary moment.
5. Be strict: only score >=6 for clear, in-the-moment action."""

_LLM_USER_TEMPLATE = """\
Classify each of these {n} commentator utterances from a live football match.
Remember: only LIVE events score above 0. Anecdotes, history, and speculation score 0.

{utterances_json}"""


# ── Public API ───────────────────────────────────────────────────────────────


def analyze_excitement(
    transcription: dict[str, Any],
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run Stage 3. Cache-aware. Two-phase: local analysis then batched LLM."""
    workspace = Path(metadata["workspace"])
    output_path = workspace / EXCITEMENT_FILENAME

    if output_path.exists():
        log.info("Stage 3 cache hit — loading existing excitement analysis")
        return json.loads(output_path.read_text())  # type: ignore[no-any-return]

    log.info("Stage 3 — excitement analysis starting")
    audio_path = workspace / AUDIO_FILENAME
    if not audio_path.exists():
        raise ExcitementError(f"audio.wav not found at {audio_path} — run Stage 2 first")

    # Phase 1: local analysis (no API calls)
    utterances = _get_commentator_utterances(transcription)
    log.info("Processing %d commentator utterances", len(utterances))

    # Compute baseline energy across the whole match for normalization
    baseline_energy = _compute_baseline_energy(audio_path, utterances)
    log.info("Baseline RMS energy: %.6f", baseline_energy)

    local: list[dict[str, Any]] = []
    for utt in utterances:
        start_s = utt["start"] / 1000.0
        end_s = utt["end"] / 1000.0
        raw_energy = _compute_energy(audio_path, start_s, end_s - start_s)
        normalized = _normalize_energy(raw_energy, baseline_energy)
        local.append(
            {
                "utterance": utt,
                "energy": normalized,
                "keywords": _match_keywords(utt["text"]),
            }
        )

    # Phase 2: batch LLM classification
    entries: list[dict[str, Any]] = []
    for batch in _chunks(local, EXCITEMENT_BATCH_SIZE):
        result_map = _classify_batch_with_llm(batch)
        for i, item in enumerate(batch):
            clf = result_map.get(i, _default_classification())
            entries.append(_build_edr_entry(item, clf).to_dict())

    output_path.write_text(json.dumps(entries, indent=2))
    log.info("Stage 3 complete — %d entries, saved to %s", len(entries), output_path)
    return entries


# ── Private helpers ──────────────────────────────────────────────────────────


def _get_commentator_utterances(transcription: dict[str, Any]) -> list[dict[str, Any]]:
    """Filter utterances to only commentator_speakers."""
    commentators = set(transcription.get("commentator_speakers", []))
    return [u for u in transcription.get("utterances", []) if u["speaker"] in commentators]


def _compute_energy(audio_path: Path, start_s: float, duration_s: float) -> float:
    """librosa RMS energy, raw mean. Returns 0.0 for zero-duration."""
    if duration_s <= 0:
        return 0.0
    y, _sr = librosa.load(str(audio_path), offset=start_s, duration=duration_s, sr=None)
    rms = librosa.feature.rms(y=y)
    return float(np.mean(rms))


def _compute_baseline_energy(
    audio_path: Path,
    utterances: list[dict[str, Any]],
) -> float:
    """Mean RMS across all commentator utterances — used as normalization baseline.

    Returns a small fallback if there are no utterances or energy is near zero.
    """
    if not utterances:
        return 0.01
    energies: list[float] = []
    for utt in utterances:
        start_s = utt["start"] / 1000.0
        dur = (utt["end"] - utt["start"]) / 1000.0
        e = _compute_energy(audio_path, start_s, dur)
        if e > 0:
            energies.append(e)
    if not energies:
        return 0.01
    return float(np.mean(energies))


def _normalize_energy(raw_energy: float, baseline: float) -> float:
    """Ratio of raw energy to baseline, capped at 1.0.

    A value of ~0.5 means average, >0.7 is notably above baseline.
    """
    if baseline <= 0:
        return 0.0
    ratio = raw_energy / baseline
    # Scale so that 2x baseline ≈ 1.0
    return min(ratio / 2.0, 1.0)


def _match_keywords(text: str) -> list[str]:
    """Return list of matched keyword strings (case-insensitive substring)."""
    lower = text.lower()
    return [kw for kw in KEYWORD_WEIGHTS if kw in lower]


def _keyword_score(matched: list[str]) -> float:
    """Sum weights of matched keywords, capped at 1.0."""
    total = sum(KEYWORD_WEIGHTS[kw] for kw in matched)
    return min(total, 1.0)


def _classify_batch_with_llm(
    batch: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Call OpenAI for a batch. Returns index-keyed map.

    Retries once if indices are missing. Falls back to defaults with log.warning.
    """
    use_azure = bool(AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT)
    use_openai = bool(OPENAI_API_KEY and OPENAI_MODEL)
    if not use_azure and not use_openai:
        raise ExcitementError(
            "Set either Azure OpenAI (AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, "
            "AZURE_OPENAI_DEPLOYMENT) or OpenAI (OPENAI_API_KEY, OPENAI_MODEL) in your .env file"
        )

    utterances_payload = [
        {
            "index": i,
            "timestamp": _format_match_time(item["utterance"]),
            "text": item["utterance"]["text"],
            "keywords": item["keywords"],
        }
        for i, item in enumerate(batch)
    ]
    expected_indices = set(range(len(batch)))
    client: Any
    if use_azure:
        client = openai.AzureOpenAI(
            api_key=AZURE_OPENAI_API_KEY,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_version=AZURE_OPENAI_API_VERSION,
        )
        model_name = AZURE_OPENAI_DEPLOYMENT
    else:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        model_name = OPENAI_MODEL

    def _call() -> dict[int, dict[str, Any]]:
        response = client.chat.completions.create(  # type: ignore[call-overload]
            model=model_name,
            messages=[
                {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _LLM_USER_TEMPLATE.format(
                        n=len(utterances_payload),
                        utterances_json=json.dumps(utterances_payload),
                    ),
                },
            ],
            response_format={"type": "json_schema", "json_schema": BATCH_RESPONSE_SCHEMA},
        )
        content = response.choices[0].message.content
        parsed: dict[str, Any] = json.loads(content)
        result: dict[int, dict[str, Any]] = {}
        for clf in parsed["classifications"]:
            idx = int(clf["index"])
            try:
                event_type = EventType(clf["event_type"])
            except ValueError:
                event_type = EventType.OTHER
            score = max(0.0, min(10.0, float(clf["excitement_score"])))
            result[idx] = {
                "index": idx,
                "event_type": event_type.value,
                "description": clf["description"],
                "excitement_score": score,
            }
        return result

    try:
        result_map = _call()
    except openai.OpenAIError as exc:
        raise ExcitementError(f"OpenAI API error: {exc}") from exc

    missing = expected_indices - set(result_map.keys())
    if missing:
        log.warning("LLM missing indices %s on first call — retrying", sorted(missing))
        try:
            retry_map = _call()
        except openai.OpenAIError as exc:
            raise ExcitementError(f"OpenAI API error on retry: {exc}") from exc
        for idx in missing:
            if idx in retry_map:
                result_map[idx] = retry_map[idx]
        still_missing = expected_indices - set(result_map.keys())
        if still_missing:
            log.warning(
                "LLM still missing indices %s after retry — using defaults",
                sorted(still_missing),
            )
            for idx in still_missing:
                result_map[idx] = _default_classification()

    return result_map


def _format_match_time(utterance: dict[str, Any]) -> str:
    """Human-readable match time for the LLM prompt, e.g. ``35:09``."""
    start_s = utterance["start"] / 1000.0
    minutes = int(start_s) // 60
    secs = int(start_s) % 60
    return f"{minutes}:{secs:02d}"


def _default_classification() -> dict[str, Any]:
    """Default classification for utterances the LLM failed to classify."""
    return {
        "index": -1,
        "event_type": EventType.OTHER.value,
        "description": "",
        "excitement_score": 0.0,
    }


def _compute_final_score(energy: float, keyword_score: float, llm_score: float) -> float:
    """Apply weighted combination from settings.

    All three inputs are scaled to 0–10 before weighting.
    """
    energy_score = energy * 10.0
    kw_score = keyword_score * 10.0
    return (
        EXCITEMENT_ENERGY_WEIGHT * energy_score
        + EXCITEMENT_KEYWORD_WEIGHT * kw_score
        + EXCITEMENT_LLM_WEIGHT * llm_score
    )


def _build_edr_entry(
    item: dict[str, Any],
    classification: dict[str, Any],
) -> ExcitementEntry:
    """Combine local analysis + LLM result into one ExcitementEntry."""
    utt = item["utterance"]
    energy = item["energy"]
    keywords = item["keywords"]
    kw_score = _keyword_score(keywords)
    llm_score = float(classification["excitement_score"])
    final = _compute_final_score(energy, kw_score, llm_score)
    return ExcitementEntry(
        timestamp_start=utt["start"] / 1000.0,
        timestamp_end=utt["end"] / 1000.0,
        commentator_energy=energy,
        commentator_text=utt["text"],
        keyword_matches=keywords,
        event_type=EventType(classification["event_type"]),
        llm_description=classification["description"],
        llm_excitement_score=llm_score,
        final_score=final,
        include_in_highlights=final >= EXCITEMENT_THRESHOLD,
    )


def _chunks(lst: list[Any], n: int) -> Generator[list[Any], None, None]:
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]
