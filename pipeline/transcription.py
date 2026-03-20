"""Stage 2 — Audio extraction, AssemblyAI transcription, and speaker diarization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import assemblyai as aai

from config.settings import ASSEMBLYAI_API_KEY, COMMENTATOR_TIME_RATIO
from utils.ffmpeg import FFmpegError, extract_audio
from utils.logger import get_logger

log = get_logger(__name__)

AUDIO_FILENAME = "audio.wav"
TRANSCRIPTION_FILENAME = "transcription.json"

FIRST_HALF_KEYWORDS: list[str] = [
    "kick off",
    "kicked off",
    "kicks off",
    "we're off",
    "we are off",
    "we're away",
    "the match begins",
    "the match is underway",
    "game is underway",
    "match is underway",
    "and we are underway",
    "and we're underway",
]
# Intentionally excludes bare "underway" and "here we go" — too generic,
# frequently appears in pre-match commentary and ad breaks.

SECOND_HALF_KEYWORDS: list[str] = [
    "second half",
    "second 45",
    "back underway",
    "second period",
    "second half underway",
    "second half is underway",
    "second half kicks off",
    "second half under way",
    "second 45 minutes",
]

_SECOND_HALF_GUARD_SECONDS = 1800  # 30 minutes after first-half kickoff


class TranscriptionError(Exception):
    """Raised when transcription or audio extraction fails."""


# ── Public API ──────────────────────────────────────────────────────────────


def identify_commentators(
    utterances: list[dict[str, Any]],
    *,
    time_ratio: float = COMMENTATOR_TIME_RATIO,
) -> list[str]:
    """Pick the commentator speaker(s) from a list of diarised utterances.

    Each utterance must have ``"speaker"`` (str), ``"start"`` (ms) and
    ``"end"`` (ms) keys.

    The speaker with the most total speaking time is always included.
    Any additional speaker whose total time is at least *time_ratio* of the
    top speaker's time is also included (co-commentator).

    Returns a sorted list of speaker labels, e.g. ``["A", "B"]``.
    """
    if not utterances:
        return []

    totals: dict[str, float] = {}
    for utt in utterances:
        speaker = utt["speaker"]
        duration = utt["end"] - utt["start"]
        totals[speaker] = totals.get(speaker, 0.0) + duration

    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    top_time = ranked[0][1]

    commentators = [speaker for speaker, time in ranked if time >= top_time * time_ratio]
    return sorted(commentators)


def detect_kickoffs(
    utterances: list[dict[str, Any]],
    commentator_speakers: list[str] | None = None,
) -> dict[str, float | None]:
    """Scan utterances for first- and second-half kickoff signals.

    When *commentator_speakers* is provided, only those speakers are considered
    (reduces false positives from stadium PA, sideline reporters, etc.).

    Returns timestamps (in seconds) of the earliest matching utterance for
    each half, or ``None`` when no match is found.
    """
    # Filter to commentator utterances when speaker labels are available.
    if commentator_speakers:
        candidate_utts = [u for u in utterances if u["speaker"] in commentator_speakers]
        if not candidate_utts:
            # Fallback: use all utterances if filtering left nothing
            candidate_utts = utterances
    else:
        candidate_utts = utterances

    first_half_ms: float | None = None
    for utt in candidate_utts:
        text_lower = utt["text"].lower()
        if any(kw in text_lower for kw in FIRST_HALF_KEYWORDS) and (
            first_half_ms is None or utt["start"] < first_half_ms
        ):
            first_half_ms = utt["start"]

    second_half_ms: float | None = None
    guard_ms: float | None = None
    if first_half_ms is not None:
        guard_ms = first_half_ms + _SECOND_HALF_GUARD_SECONDS * 1000

    for utt in candidate_utts:
        if guard_ms is not None and utt["start"] <= guard_ms:
            continue
        text_lower = utt["text"].lower()
        if any(kw in text_lower for kw in SECOND_HALF_KEYWORDS) and (
            second_half_ms is None or utt["start"] < second_half_ms
        ):
            second_half_ms = utt["start"]

    kickoff_first = first_half_ms / 1000 if first_half_ms is not None else None
    kickoff_second = second_half_ms / 1000 if second_half_ms is not None else None

    log.info(
        "Kickoff detection — first_half: %s s, second_half: %s s",
        kickoff_first,
        kickoff_second,
    )
    return {
        "kickoff_first_half": kickoff_first,
        "kickoff_second_half": kickoff_second,
    }


def transcribe(metadata: dict[str, Any]) -> dict[str, Any]:
    """Run Stage 2 of the pipeline.

    1. Extract audio from the video (FFmpeg).
    2. Transcribe with AssemblyAI (speaker diarization enabled).
    3. Identify the commentator speaker(s).
    4. Save everything to ``transcription.json``.

    Returns the transcription data dict.
    Skips if ``transcription.json`` already exists (cache hit).
    """
    workspace = Path(metadata["workspace"])
    transcription_path = workspace / TRANSCRIPTION_FILENAME

    if transcription_path.exists():
        log.info("Stage 2 cache hit — loading existing transcription")
        cached: dict[str, Any] = json.loads(transcription_path.read_text())
        if "kickoff_first_half" not in cached:
            kickoffs = detect_kickoffs(
                cached.get("utterances", []),
                commentator_speakers=cached.get("commentator_speakers"),
            )
            cached.update(kickoffs)
            transcription_path.write_text(json.dumps(cached, indent=2))
            log.info("Backfilled kickoff fields into cached transcription")
        return cached

    log.info("Stage 2 — transcription starting")

    # 2a. Extract audio
    video_path = workspace / metadata["video_filename"]
    audio_path = workspace / AUDIO_FILENAME
    if not audio_path.exists():
        try:
            extract_audio(video_path, audio_path)
        except FFmpegError as exc:
            raise TranscriptionError(str(exc)) from exc
    else:
        log.info("Audio already extracted, skipping")

    # 2b. Transcribe with AssemblyAI
    utterances = _call_assemblyai(audio_path)

    # 2c. Identify commentators
    commentator_labels = identify_commentators(utterances)
    log.info(
        "Identified %d commentator(s): %s",
        len(commentator_labels),
        ", ".join(commentator_labels),
    )

    # 2d. Detect kickoff timestamps (commentator-filtered)
    kickoffs = detect_kickoffs(utterances, commentator_speakers=commentator_labels)

    # 2e. Build and cache result
    result: dict[str, Any] = {
        "audio_filename": AUDIO_FILENAME,
        "total_utterances": len(utterances),
        "commentator_speakers": commentator_labels,
        "utterances": utterances,
        **kickoffs,
    }

    transcription_path.write_text(json.dumps(result, indent=2))
    log.info("Stage 2 complete — saved to %s", transcription_path)
    return result


# ── Private helpers ─────────────────────────────────────────────────────────


def _call_assemblyai(audio_path: Path) -> list[dict[str, Any]]:
    """Send audio to AssemblyAI for transcription with speaker diarization.

    Returns a list of utterance dicts with keys:
    ``speaker``, ``text``, ``start`` (ms), ``end`` (ms).
    """
    if not ASSEMBLYAI_API_KEY:
        raise TranscriptionError("ASSEMBLYAI_API_KEY not set — add it to your .env file")

    aai.settings.api_key = ASSEMBLYAI_API_KEY

    config = aai.TranscriptionConfig(speaker_labels=True)
    transcriber = aai.Transcriber()

    log.info("Uploading audio to AssemblyAI (%s)…", audio_path.name)
    transcript = transcriber.transcribe(str(audio_path), config=config)

    if transcript.status == aai.TranscriptStatus.error:
        raise TranscriptionError(f"AssemblyAI transcription failed: {transcript.error}")

    if not transcript.utterances:
        raise TranscriptionError("AssemblyAI returned no utterances — diarization may have failed")

    utterances: list[dict[str, Any]] = [
        {
            "speaker": u.speaker,
            "text": u.text,
            "start": u.start,
            "end": u.end,
        }
        for u in transcript.utterances
    ]

    speaker_count = len({u["speaker"] for u in utterances})
    log.info(
        "Transcription complete — %d utterances, %d speakers",
        len(utterances),
        speaker_count,
    )
    return utterances
