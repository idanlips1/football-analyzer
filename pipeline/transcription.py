"""Stage 2 — Audio extraction, AssemblyAI transcription, and speaker diarization."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import assemblyai as aai

from config.settings import ASSEMBLYAI_API_KEY, COMMENTATOR_TIME_RATIO
from utils.ffmpeg import FFmpegError, extract_audio
from utils.logger import get_logger
from utils.storage import StorageBackend, StorageError

log = get_logger(__name__)

AUDIO_FILENAME = "audio.wav"
TRANSCRIPTION_FILENAME = "transcription.json"

FIRST_HALF_KEYWORDS: list[str] = [
    "kick off",
    "kicked off",
    "underway",
    "we're off",
    "here we go",
    "the match begins",
    "we are off",
    "we're away",
]

# Action phrases that signal an actual kickoff happening (not a narrative mention).
_KICKOFF_ACTION_PHRASES: list[str] = [
    "kick off",
    "kicked off",
    "underway",
    "we're off",
    "here we go",
    "we are off",
    "we're away",
    "restart",
    "resumes",
    "get going",
    "gets going",
]

# Narrative context words — if present alongside "second half", it's likely
# the commentator is talking *about* a second half, not announcing one.
_NARRATIVE_CONTEXT_WORDS: list[str] = [
    "against",
    "played",
    "came on",
    "scored in",
    "remember",
    "last season",
    "last year",
    "in the second half",
]

SECOND_HALF_KEYWORDS: list[str] = [
    "second half",
    "second 45",
    "back underway",
    "second period",
    "half underway",
]

# Phrases that reliably signal halftime has been reached or is imminent
# (used to anchor second-half detection when there's no silence gap).
# These are intentionally specific to avoid false positives like
# "10 minutes to go to half time".
_HALFTIME_KEYWORDS: list[str] = [
    "into the break",
    "the interval",
    "added time",
    "stoppage time",
    "end of the first half",
    "end of the half",
    "first half over",
    "that's the half",
    "brings the first half",
    "blows for half",
    "blows his whistle",
]

_SECOND_HALF_GUARD_SECONDS = 1800  # 30 minutes after first-half kickoff

# Absolute minimum seconds from video start before accepting a second-half
# keyword, regardless of whether first-half kickoff was detected.
_ABSOLUTE_GUARD_SECONDS = 2100  # 35 minutes

# Gap detection: the halftime break typically produces the longest utterance
# gap in the video.  We search for it inside this window (seconds from start).
_GAP_SEARCH_START = 1800  # 30 min — earliest reasonable halftime
_GAP_SEARCH_END = 4200  # 70 min — latest reasonable halftime
_MIN_GAP_SECONDS = 15  # lowered — some broadcasts talk through halftime


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


def _find_halftime_gap(utterances: list[dict[str, Any]]) -> float | None:
    """Find the halftime break by locating the longest utterance gap.

    Searches between ``_GAP_SEARCH_START`` and ``_GAP_SEARCH_END`` seconds
    from the video start.  Returns the timestamp (seconds) of the first
    utterance *after* the longest gap, which is a strong proxy for the
    second-half kickoff.  Returns ``None`` if no gap ≥ ``_MIN_GAP_SECONDS``
    is found.
    """
    start_ms = _GAP_SEARCH_START * 1000
    end_ms = _GAP_SEARCH_END * 1000

    # Collect utterance boundaries inside the search window.
    endpoints: list[tuple[int, int]] = []
    for utt in utterances:
        if utt["end"] < start_ms:
            continue
        if utt["start"] > end_ms:
            break  # utterances are chronological
        endpoints.append((utt["start"], utt["end"]))

    if not endpoints:
        return None

    best_gap = 0.0
    best_resume_ms: int | None = None

    for i in range(1, len(endpoints)):
        gap_s = (endpoints[i][0] - endpoints[i - 1][1]) / 1000
        if gap_s > best_gap and gap_s >= _MIN_GAP_SECONDS:
            best_gap = gap_s
            best_resume_ms = endpoints[i][0]

    if best_resume_ms is None:
        return None

    log.info(
        "Halftime gap detected: %.0f s gap, commentary resumes at %.1f s",
        best_gap,
        best_resume_ms / 1000,
    )
    return best_resume_ms / 1000


def _is_narrative_mention(text_lower: str) -> bool:
    """Return ``True`` if the utterance is *talking about* a second half
    rather than *announcing* one (e.g. "he came on for the second half
    against Bayern Munich").
    """
    return any(ctx in text_lower for ctx in _NARRATIVE_CONTEXT_WORDS)


def _has_kickoff_action(text_lower: str) -> bool:
    """Return ``True`` if the utterance contains an action phrase that
    signals a kickoff is actually happening.
    """
    return any(phrase in text_lower for phrase in _KICKOFF_ACTION_PHRASES)


# Phrases within a bridge utterance that signal live match commentary has
# resumed (as opposed to halftime studio analysis).
_LIVE_COMMENTARY_SIGNALS: list[str] = [
    # Explicit restart signals
    "kick off",
    "kicked off",
    "underway",
    "restart",
    "resumes",
    "second half",
    "second 45",
    "we're back",
    "we are back",
    "back under way",
    # Live-action phrases (present tense)
    "plays it",
    "passes it",
    "crosses",
    "header",
    "shoots",
    "shot",
    "tackle",
    "throw in",
    "throw-in",
    "corner kick",
    "free kick",
    "offside",
    "the referee",
    "yellow card",
    "foul by",
    "wins the ball",
    "on the ball",
    "into the box",
    "down the wing",
    "on the break",
]


def _scan_bridge_words(utt: dict[str, Any]) -> float | None:
    """Scan word-level timestamps inside a bridge utterance for the point
    where live match commentary resumes after halftime analysis.

    Returns the timestamp (seconds) of the first live-commentary signal
    found in the second half of the utterance, or ``None`` if no words
    are available or no signal is found.
    """
    words: list[dict[str, Any]] = utt.get("words", [])
    if not words:
        return None

    utt_start = utt["start"]
    utt_end = utt["end"]
    utt_midpoint = (utt_start + utt_end) / 2

    # Build a running text window from words in the second half of the
    # utterance and check for live-commentary signals.
    # Use a sliding window of ~5 words for phrase matching.
    second_half_words = [w for w in words if w["start"] >= utt_midpoint]
    if not second_half_words:
        return None

    for i, word in enumerate(second_half_words):
        # Build a small context window (current word + next 4)
        window_words = second_half_words[i : i + 5]
        window_text = " ".join(w["text"] for w in window_words).lower()

        for signal in _LIVE_COMMENTARY_SIGNALS:
            if signal in window_text:
                kickoff_s = float(word["start"]) / 1000
                log.info(
                    "Bridge word scan: live commentary signal '%s' at %.1f s",
                    signal,
                    kickoff_s,
                )
                return kickoff_s

    return None


def _find_halftime_keyword(utterances: list[dict[str, Any]]) -> float | None:
    """Find a halftime marker via keyword scanning and estimate the
    second-half kickoff from what follows.

    Scans the 35–65 min window for phrases like "half time", "added time",
    "the interval".  Once found, looks for the kickoff by:

    1. First check for an explicit kickoff-action phrase after the marker.
    2. If none found, look for a long "bridge" utterance that spans halftime
       (AssemblyAI sometimes merges the halftime analysis into one giant
       utterance).  The second half starts roughly when this utterance ends.
    """
    search_start_ms = _ABSOLUTE_GUARD_SECONDS * 1000
    search_end_ms = 3300 * 1000  # 55 min — halftime should be before this

    # Step 1: find the earliest halftime marker in the window.
    halftime_ms: float | None = None
    for utt in utterances:
        if utt["start"] < search_start_ms:
            continue
        if utt["start"] > search_end_ms:
            break
        text_lower = utt["text"].lower()
        if any(kw in text_lower for kw in _HALFTIME_KEYWORDS):
            halftime_ms = utt["start"]
            break  # take the first match — it's closest to actual halftime

    if halftime_ms is None:
        return None

    log.info(
        "Halftime keyword found at %.1f s — scanning for kickoff after",
        halftime_ms / 1000,
    )

    # Step 2a: look for a long "bridge" utterance that spans the halftime
    # break — AssemblyAI sometimes merges the halftime analysis into one
    # giant utterance.  If word-level timestamps are available, scan for
    # the transition point within the utterance.
    bridge_deadline_ms = halftime_ms + 120 * 1000  # 2 min
    bridge_utt: dict[str, Any] | None = None
    longest_dur = 0.0
    for utt in utterances:
        if utt["start"] < halftime_ms:
            continue
        if utt["start"] > bridge_deadline_ms:
            break
        dur = utt["end"] - utt["start"]
        if dur > longest_dur:
            longest_dur = dur
            bridge_utt = utt

    bridge_kickoff: float | None = None
    if bridge_utt is not None and longest_dur > 60_000:
        # Try word-level scanning first for precision.
        word_kickoff = _scan_bridge_words(bridge_utt)
        bridge_kickoff = word_kickoff if word_kickoff is not None else bridge_utt["end"] / 1000
        log.info(
            "Second-half estimated from bridge utterance: %.1f s (duration %.0f s, word-level=%s)",
            bridge_kickoff,
            longest_dur / 1000,
            word_kickoff is not None,
        )

    # Step 2b: look for an explicit kickoff action phrase after the marker.
    action_kickoff: float | None = None
    action_deadline_ms = halftime_ms + 1200 * 1000  # 20 min
    for utt in utterances:
        if utt["start"] <= halftime_ms:
            continue
        if utt["start"] > action_deadline_ms:
            break
        text_lower = utt["text"].lower()
        if _has_kickoff_action(text_lower):
            action_kickoff = utt["start"] / 1000
            log.info(
                "Second-half kickoff action at %.1f s (halftime+%.0f s)",
                action_kickoff,
                (utt["start"] - halftime_ms) / 1000,
            )
            break

        if any(kw in text_lower for kw in SECOND_HALF_KEYWORDS) and not _is_narrative_mention(
            text_lower
        ):
            action_kickoff = utt["start"] / 1000
            log.info(
                "Second-half keyword at %.1f s (halftime+%.0f s)",
                action_kickoff,
                (utt["start"] - halftime_ms) / 1000,
            )
            break

    # Prefer bridge (closer to halftime, more anchored) over keyword
    # (which can be minutes into the second half).
    # If both exist and action is earlier than bridge, trust action.
    if bridge_kickoff is not None and action_kickoff is not None:
        return min(bridge_kickoff, action_kickoff)
    return bridge_kickoff or action_kickoff


def detect_kickoffs(utterances: list[dict[str, Any]]) -> dict[str, float | None]:
    """Scan utterances for first- and second-half kickoff signals.

    Uses four complementary strategies for the second half (in priority order):

    1. **Halftime keyword + action** — find "half time" / "added time" then
       the next kickoff action phrase after it.
    2. **Gap detection** — find the longest commentary silence in the
       30–70 min window (the halftime break).
    3. **Context-aware keyword matching** — require action phrases near
       "second half" and reject narrative mentions.
    4. **Absolute guard** — never accept a second-half signal earlier than
       35 min from video start, even if first-half detection failed.

    Returns timestamps (in seconds) or ``None``.
    """
    # ── First half ──────────────────────────────────────────────
    first_half_ms: float | None = None
    for utt in utterances:
        text_lower = utt["text"].lower()
        if any(kw in text_lower for kw in FIRST_HALF_KEYWORDS) and (
            first_half_ms is None or utt["start"] < first_half_ms
        ):
            first_half_ms = utt["start"]

    # ── Second half: strategy A — halftime keyword + action ─────
    halftime_kickoff = _find_halftime_keyword(utterances)

    # ── Second half: strategy B — gap detection ─────────────────
    gap_kickoff = _find_halftime_gap(utterances)

    # ── Second half: strategy C — keyword matching with guards ──
    keyword_kickoff_ms: float | None = None

    # Guard: use first-half-relative guard OR absolute guard, whichever later.
    first_half_guard = (
        first_half_ms + _SECOND_HALF_GUARD_SECONDS * 1000 if first_half_ms is not None else 0
    )
    absolute_guard = _ABSOLUTE_GUARD_SECONDS * 1000
    guard_ms = max(first_half_guard, absolute_guard)

    for utt in utterances:
        if utt["start"] <= guard_ms:
            continue
        text_lower = utt["text"].lower()
        has_kw = any(kw in text_lower for kw in SECOND_HALF_KEYWORDS)
        if not has_kw:
            continue
        # Reject narrative mentions unless an action phrase is also present.
        if _is_narrative_mention(text_lower) and not _has_kickoff_action(text_lower):
            log.debug("Skipping narrative second-half mention: %.1f s", utt["start"] / 1000)
            continue
        if keyword_kickoff_ms is None or utt["start"] < keyword_kickoff_ms:
            keyword_kickoff_ms = utt["start"]

    keyword_kickoff = keyword_kickoff_ms / 1000 if keyword_kickoff_ms is not None else None

    # ── Combine: halftime-anchored > gap > keyword ──────────────
    second_half_kickoff: float | None = None
    if halftime_kickoff is not None:
        # Halftime anchor is most reliable — commentary explicitly said
        # "half time" / "added time" and we found the next action phrase.
        second_half_kickoff = halftime_kickoff
    elif gap_kickoff is not None and keyword_kickoff is not None:
        # If both agree within 3 minutes, use the keyword (more precise).
        if abs(gap_kickoff - keyword_kickoff) <= 180:
            second_half_kickoff = keyword_kickoff
        else:
            second_half_kickoff = gap_kickoff
    elif gap_kickoff is not None:
        second_half_kickoff = gap_kickoff
    else:
        second_half_kickoff = keyword_kickoff

    kickoff_first = first_half_ms / 1000 if first_half_ms is not None else None

    log.info(
        "Kickoff detection — first_half: %s s, second_half: %s s "
        "(halftime_kw=%s s, gap=%s s, keyword=%s s)",
        kickoff_first,
        second_half_kickoff,
        halftime_kickoff,
        gap_kickoff if gap_kickoff is not None else None,
        keyword_kickoff,
    )
    return {
        "kickoff_first_half": kickoff_first,
        "kickoff_second_half": second_half_kickoff,
    }


def transcribe(metadata: dict[str, Any], storage: StorageBackend) -> dict[str, Any]:
    """Run Stage 2 of the pipeline.

    1. Extract audio from the video (FFmpeg).
    2. Transcribe with AssemblyAI (speaker diarization enabled).
    3. Identify the commentator speaker(s).
    4. Save everything to ``transcription.json``.

    Returns the transcription data dict.
    Skips if ``transcription.json`` already exists (cache hit).
    """
    video_id: str = metadata["video_id"]

    try:
        cached: dict[str, Any] = storage.read_json(video_id, TRANSCRIPTION_FILENAME)
        log.info("Stage 2 cache hit — loading existing transcription")
        if "kickoff_first_half" not in cached:
            kickoffs = detect_kickoffs(cached.get("utterances", []))
            cached.update(kickoffs)
            storage.write_json(video_id, TRANSCRIPTION_FILENAME, cached)
            log.info("Backfilled kickoff fields into cached transcription")
        return cached
    except StorageError:
        pass

    log.info("Stage 2 — transcription starting")

    # 2a. Extract audio
    workspace = storage.workspace_path(video_id)
    video_path = workspace / metadata["video_filename"]
    audio_path = storage.local_path(video_id, AUDIO_FILENAME)
    if not audio_path.exists():
        log.info("Extracting audio from %s (this may take a few minutes)…", video_path.name)
        t0 = time.monotonic()
        try:
            extract_audio(video_path, audio_path)
        except FFmpegError as exc:
            raise TranscriptionError(str(exc)) from exc
        elapsed = time.monotonic() - t0
        log.info("Audio extraction completed in %.1f s", elapsed)
    else:
        size_mb = audio_path.stat().st_size / (1024 * 1024)
        log.info("Audio already extracted (%.0f MB), skipping", size_mb)

    # 2b. Transcribe with AssemblyAI
    log.info("Starting AssemblyAI transcription (this is the slowest step)…")
    t0 = time.monotonic()
    utterances = _call_assemblyai(audio_path)
    elapsed = time.monotonic() - t0
    log.info("AssemblyAI transcription finished in %.1f s (%.1f min)", elapsed, elapsed / 60)

    # 2c. Identify commentators
    commentator_labels = identify_commentators(utterances)
    log.info(
        "Identified %d commentator(s): %s",
        len(commentator_labels),
        ", ".join(commentator_labels),
    )

    # 2d. Detect kickoff timestamps
    log.info("Scanning utterances for kickoff timestamps…")
    kickoffs = detect_kickoffs(utterances)

    # 2e. Build and cache result
    result: dict[str, Any] = {
        "audio_filename": AUDIO_FILENAME,
        "total_utterances": len(utterances),
        "commentator_speakers": commentator_labels,
        "utterances": utterances,
        **kickoffs,
    }

    storage.write_json(video_id, TRANSCRIPTION_FILENAME, result)
    log.info("Stage 2 complete — saved %s for %s", TRANSCRIPTION_FILENAME, video_id)
    return result


# ── Private helpers ─────────────────────────────────────────────────────────


def _call_assemblyai(audio_path: Path) -> list[dict[str, Any]]:
    """Send audio to AssemblyAI for transcription with speaker diarization.

    Returns a list of utterance dicts with keys:
    ``speaker``, ``text``, ``start`` (ms), ``end`` (ms), and ``words``
    (list of ``{text, start, end}`` dicts for word-level timestamps).
    """
    if not ASSEMBLYAI_API_KEY:
        raise TranscriptionError("ASSEMBLYAI_API_KEY not set — add it to your .env file")

    aai.settings.api_key = ASSEMBLYAI_API_KEY

    config = aai.TranscriptionConfig(speaker_labels=True)
    transcriber = aai.Transcriber()

    audio_size_mb = audio_path.stat().st_size / (1024 * 1024)
    log.info(
        "Uploading audio to AssemblyAI (%s, %.0f MB) — "
        "upload + transcription + diarization may take 5–15 min…",
        audio_path.name,
        audio_size_mb,
    )
    t0 = time.monotonic()
    transcript = transcriber.transcribe(str(audio_path), config=config)
    elapsed = time.monotonic() - t0
    log.info(
        "AssemblyAI returned status=%s in %.1f s (%.1f min)",
        transcript.status,
        elapsed,
        elapsed / 60,
    )

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
            "words": [{"text": w.text, "start": w.start, "end": w.end} for w in (u.words or [])],
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
