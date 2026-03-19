# API-Driven Highlights Pipeline

**Date:** 2026-03-19
**Status:** Approved

## Problem

The current pipeline detects highlights by analyzing commentator voice (energy + keywords + LLM classification). This produces inaccurate event identification, imprecise timestamps, and jumpy clips that aren't fun to watch. The LLM excitement scoring is expensive and unreliable — it confuses retrospective commentary with live action and can't precisely identify what type of event occurred.

## Solution

Replace the excitement/EDR/filtering stages with an API-driven approach:

1. **API-Football** provides structured match event data (goals, cards, penalties, VAR) with exact match minutes, player names, and running scores — a ground-truth event timeline.
2. **Existing transcription** is repurposed for kickoff detection and audio-based timestamp refinement.
3. **Event-type-specific clip windows** replace the blunt excitement-threshold approach, giving each event type appropriate build-up and aftermath durations.
4. **Interactive CLI** lets users search for matches by name instead of pasting YouTube URLs.

## Architecture

```
User query (e.g. "Champions League final 2024")
  → [1: match_finder]    — YouTube search + download, API-Football fixture lookup
  → [2: match_events]    — fetch events from API-Football, cache as match_events.json
  → [3: transcription]   — AssemblyAI transcription (reused), kickoff/halftime detection
  → [4: event_aligner]   — map match minutes → video timestamps using kickoff offsets + audio refinement
  → [5: clip_builder]    — event-type clip windows, merge overlapping, cut + concat with FFmpeg
```

Old pipeline modules (`excitement.py`, `edr.py`, `filtering.py`) remain in the repo but are no longer called. They can be deprecated later.

## File Layout

### New/modified files

```
pipeline/
  match_finder.py     # NEW — interactive CLI, YouTube search, API-Football fixture lookup
  match_events.py     # NEW — fetch + cache API-Football events
  transcription.py    # EXISTING — reused, plus kickoff detection function
  event_aligner.py    # NEW — map match minutes → video timestamps
  clip_builder.py     # NEW — event-type windows, cut + concat

config/
  settings.py         # MODIFIED — add API_FOOTBALL_KEY, event clip durations
  clip_windows.py     # NEW — per-event-type pre/post roll durations

main.py              # REWRITTEN — interactive CLI loop replaces argparse
```

### Workspace output per match

```
pipeline_workspace/<video_id>/
  metadata.json          # from match_finder (includes fixture_id)
  match_events.json      # from match_events (API-Football data)
  transcription.json     # from transcription (includes kickoff timestamps)
  audio.wav              # from transcription
  aligned_events.json    # from event_aligner (events with video timestamps)
  clip_manifest.json     # from clip_builder (cut points for debugging)
  clips/                 # individual clip files
  highlights.mp4         # final output
```

## Stage Details

### Stage 1: Match Finder (`pipeline/match_finder.py`)

**Input:** User text query or YouTube URL.

**Logic:**
- If input is a URL → pass directly to yt-dlp download (reuse existing ingestion logic).
- If input is a text query:
  - **YouTube search:** Use yt-dlp's `ytsearch:` prefix (e.g., `ytsearch5:"Champions League final 2024 full match"`). Pick the longest result over 45 minutes.
  - **API-Football lookup:** Search fixtures by team names + date parsed from the YouTube title.
  - Show both results to the user, ask for confirmation.
- Download video, save `metadata.json` (same format as today, plus `fixture_id` field).

**Output:** `metadata.json` with video file path and `fixture_id`.

### Stage 2: Match Events (`pipeline/match_events.py`)

**Input:** `fixture_id` from Stage 1.

**Logic:**
- Call `GET https://apiv3.apifootball.com/?action=get_events&match_id=<id>&APIkey=<key>`
- Parse response into internal `MatchEvent` dataclass:
  ```
  MatchEvent:
    minute: int
    extra_minute: int | None
    half: str                 # "1st Half" / "2nd Half" / "Extra Time"
    event_type: EventType     # goal, yellow_card, red_card, substitution, penalty, var
    team: str
    player: str
    assist: str | None
    score: str                # running score "2 - 1"
    detail: str               # "Normal Goal", "Penalty", "Own Goal", etc.
  ```
- Filter out substitutions by default (configurable).
- Cache as `match_events.json`.

**Environment:** Requires `API_FOOTBALL_KEY` in `.env`.

### Stage 3: Transcription + Kickoff Detection

**Input:** Video file from Stage 1.

**Logic:**
- Run existing AssemblyAI transcription (unchanged).
- **New: Kickoff detection.** Scan utterances for keywords:
  - First half: "kick off", "underway", "we're off", "here we go", "the match begins"
  - Second half: "second half", "second 45", "back underway", "restart"
- Corroborate candidates with audio energy (crowd noise spike at kickoff).
- If detection fails, fall back to asking the user via CLI.

**Output:** `transcription.json` with two new fields:
```json
{
  "kickoff_first_half": 330.5,
  "kickoff_second_half": 3420.0
}
```

### Stage 4: Event Aligner (`pipeline/event_aligner.py`)

**Input:** `match_events.json` + `transcription.json` (with kickoff timestamps).

**Mapping formula:**
```
1st half (minute 1–45+):
  video_ts = kickoff_first_half + (minute × 60)

2nd half (minute 46–90+):
  video_ts = kickoff_second_half + ((minute − 45) × 60)

Stoppage time (e.g. 90+3'):
  video_ts = kickoff_second_half + ((90 − 45) × 60) + (extra_minute × 60)
```

**Audio refinement:** The formula gives an approximate timestamp (within ~30-60s). To fine-tune:
1. Open a ±60s search window around the estimated timestamp.
2. Find the energy peak in transcription utterances within that window.
3. Snap the event timestamp to the start of the peak utterance.

**Output:** `aligned_events.json` with `estimated_video_ts`, `refined_video_ts`, and `confidence` per event.

### Stage 5: Clip Builder (`pipeline/clip_builder.py`)

**Input:** `aligned_events.json` + source video.

**Clip windows** (defined in `config/clip_windows.py`):

| Event Type   | Pre-roll | Post-roll | Total  |
|-------------|----------|-----------|--------|
| Goal         | 15s      | 30s       | ~45s   |
| Penalty      | 10s      | 25s       | ~35s   |
| Red Card     | 10s      | 15s       | ~25s   |
| Yellow Card  | 5s       | 10s       | ~15s   |
| Near Miss    | 10s      | 15s       | ~25s   |
| Save         | 10s      | 15s       | ~25s   |
| VAR Review   | 5s       | 20s       | ~25s   |
| Own Goal     | 10s      | 25s       | ~35s   |
| Default      | 10s      | 15s       | ~25s   |

**Logic:**
1. Calculate raw cut points: `clip_start = refined_video_ts - pre_roll`, `clip_end = refined_video_ts + post_roll`.
2. Merge overlapping clips (reuse gap-merge logic from existing `edr.py`).
3. Clamp to video bounds.
4. Budget enforcement: if total exceeds target highlights length (default 10 min), drop lowest-priority events. Priority: goal > penalty > red_card > var_review > near_miss > save > yellow_card.
5. Cut + concat using existing `utils/ffmpeg.py`.

**Output:** `clip_manifest.json` + `clips/` directory + `highlights.mp4`.

## Configuration

### New environment variables

```
API_FOOTBALL_KEY=<key>        # apifootball.com API key (free tier: 100 req/day)
```

### New settings in `config/settings.py`

```python
API_FOOTBALL_KEY: str
API_FOOTBALL_BASE_URL = "https://apiv3.apifootball.com/"
DEFAULT_HIGHLIGHTS_DURATION_SECONDS = 600.0  # 10 minutes (already exists)
MERGE_GAP_SECONDS = 5.0                      # already exists, reused
```

## What We Keep

- `utils/ffmpeg.py` — all FFmpeg wrappers (cut_clip, concat_clips, extract_audio, get_video_duration)
- `utils/logger.py` — logging throughout
- `pipeline/transcription.py` — AssemblyAI transcription (augmented with kickoff detection)
- `pipeline/ingestion.py` — video download logic (reused inside match_finder)
- `models/events.py` — EventType enum, timestamp helpers
- `config/settings.py` — extended with new settings
- `tests/conftest.py` — test fixtures

## What We Deprecate

- `pipeline/excitement.py` — no longer called (LLM excitement analysis)
- `pipeline/edr.py` — replaced by event_aligner
- `pipeline/filtering.py` — replaced by clip_builder's budget enforcement
- `config/llm_schema.py` — no longer needed
- `config/keywords.py` — no longer needed for clip selection

## Testing Strategy

- TDD for new modules: write tests before implementation
- Mock API-Football responses in tests (no real API calls)
- Mock yt-dlp search results in tests
- Test kickoff detection with sample transcription data
- Test event alignment math with known fixtures
- Test clip merging with overlapping windows
- Integration test: feed known match data through full pipeline, verify clip timestamps
