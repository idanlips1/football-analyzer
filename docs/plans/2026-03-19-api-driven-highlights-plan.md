# Implementation Plan: API-Driven Highlights Pipeline

**Design doc:** `docs/plans/2026-03-19-api-driven-highlights-design.md`
**Date:** 2026-03-19

## Batch 1: Foundation — Config, Models, Clip Windows

### Task 1.1: Add `OWN_GOAL` to `EventType` enum
- **File:** `models/events.py`
- **What:** Add `OWN_GOAL = "own_goal"` to the `EventType` StrEnum.
- **Test:** Verify `EventType("own_goal") == EventType.OWN_GOAL`.

### Task 1.2: Add `MatchEvent` dataclass
- **File:** `models/events.py`
- **What:** New dataclass for API-Football events:
  ```python
  @dataclass
  class MatchEvent:
      minute: int
      extra_minute: int | None
      half: str               # "1st Half" / "2nd Half" / "Extra Time"
      event_type: EventType
      team: str
      player: str
      assist: str | None
      score: str              # "2 - 1"
      detail: str             # "Normal Goal", "Penalty", "Own Goal", etc.
  ```
- **Include:** `to_dict()` and `from_dict()` methods, consistent with existing model patterns.
- **Test:** Round-trip `to_dict` → `from_dict`, edge cases (None assist, extra_minute).

### Task 1.3: Add `AlignedEvent` dataclass
- **File:** `models/events.py`
- **What:** Output model for Stage 4:
  ```python
  @dataclass
  class AlignedEvent:
      event_type: EventType
      minute: int
      extra_minute: int | None
      half: str
      player: str
      team: str
      score: str
      detail: str
      estimated_video_ts: float
      refined_video_ts: float
      confidence: float
  ```
- **Include:** `to_dict()` / `from_dict()`.
- **Test:** Round-trip serialization.

### Task 1.4: Create `config/clip_windows.py`
- **File:** `config/clip_windows.py` (new)
- **What:** Dict mapping `EventType` → `(pre_roll, post_roll)` tuples. Plus `DEFAULT_WINDOW`, `EVENT_PRIORITY` list for budget enforcement.
- **Test:** Verify all highlight-relevant EventTypes have entries. Verify priority list ordering.

### Task 1.5: Add API-Football settings
- **File:** `config/settings.py`
- **What:** Add:
  ```python
  API_FOOTBALL_KEY: str = os.environ.get("API_FOOTBALL_KEY", "")
  API_FOOTBALL_BASE_URL = "https://apiv3.apifootball.com/"
  ```
- **Test:** No new tests needed (same pattern as existing env vars).

### Task 1.6: Update `conftest.py`
- **File:** `tests/conftest.py`
- **What:** The `tmp_workspace` fixture needs to monkeypatch the new modules as they're created. Add a helper that patches all modules at once. Also add a `sample_match_events` fixture and a `sample_transcription_with_kickoff` fixture for reuse.
- **Test:** N/A (test infrastructure).

**Checkpoint:** Run `pytest`, `ruff check .`, `mypy .` — all green.

---

## Batch 2: Match Events — API-Football Client (Stage 2)

### Task 2.1: Create `pipeline/match_events.py` — API client
- **File:** `pipeline/match_events.py` (new)
- **What:**
  - `MatchEventsError` exception class.
  - `_fetch_fixture(match_id: str) -> dict` — HTTP GET to API-Football, returns raw JSON.
  - `_parse_events(raw: dict) -> list[MatchEvent]` — extract goalscorers, cards into `MatchEvent` list. Map API detail strings ("yellow card", "Normal Goal", "Penalty") to our `EventType` enum.
  - `fetch_match_events(metadata: dict) -> list[dict]` — orchestrator. Cache-aware (reads/writes `match_events.json`). Takes metadata dict with `fixture_id`.
- **Dependencies:** `requests` (add to requirements.txt if not present) or `urllib`.
- **Test (write first):**
  - Mock the HTTP call. Feed sample API-Football JSON (from the West Ham/Newcastle example we found).
  - Verify correct parsing: 6 goals, 3 cards, correct minutes, correct players.
  - Verify caching: second call returns cached data without HTTP.
  - Verify `MatchEventsError` raised if API key missing.
  - Verify unknown event types map to `EventType.OTHER`.

### Task 2.2: Map API-Football event details to `EventType`
- **File:** `pipeline/match_events.py` (inside `_parse_events`)
- **What:** Mapping logic:
  - goalscorer entry → `EventType.GOAL` (or `PENALTY` if detail contains "Penalty", `OWN_GOAL` if "Own Goal")
  - card "yellow card" → `EventType.YELLOW_CARD`
  - card "red card" → `EventType.RED_CARD`
  - card "yellow/red" (second yellow) → `EventType.RED_CARD`
- **Test:** Cover all mapping branches, including edge cases.

**Checkpoint:** Run `pytest`, `ruff`, `mypy` — all green.

---

## Batch 3: Match Finder — YouTube Search + Fixture Lookup (Stage 1)

### Task 3.1: Create `pipeline/match_finder.py` — YouTube search
- **File:** `pipeline/match_finder.py` (new)
- **What:**
  - `MatchFinderError` exception class.
  - `search_youtube(query: str) -> list[dict]` — use yt-dlp's `ytsearch5:` to find candidates. Return list of `{title, url, duration_seconds, video_id}`. Filter to results > 45 min.
  - `_is_url(text: str) -> bool` — simple check for `http` prefix.
- **Test (write first):**
  - Mock yt-dlp `extract_info`. Verify it picks the longest video over 45 min.
  - Verify URL detection.
  - Verify empty results raise `MatchFinderError`.

### Task 3.2: Add API-Football fixture search
- **File:** `pipeline/match_finder.py`
- **What:**
  - `search_fixture(query: str) -> list[dict]` — call API-Football search. Parse team names from query or YouTube title. Search by date + team names.
  - This is fuzzy — extract likely team names, try to match. If ambiguous, return multiple candidates for user to pick.
- **Test:**
  - Mock API call. Verify parsing of team names from titles like "FULL MATCH | Liverpool 3-1 Manchester City | FA Community Shield".
  - Verify fixture ID extraction.

### Task 3.3: Orchestrator — `find_match(user_input: str) -> dict`
- **File:** `pipeline/match_finder.py`
- **What:**
  - If URL → extract video ID, download (reuse `ingestion._download_video` and `ingestion._extract_video_id`), search API-Football for fixture.
  - If text query → search YouTube + API-Football, return candidates.
  - Returns metadata dict with `fixture_id` added.
  - Cache-aware (reuse existing metadata.json pattern).
- **Test:**
  - Mock both yt-dlp and API-Football. Verify full flow from text query to metadata dict with fixture_id.

**Checkpoint:** Run `pytest`, `ruff`, `mypy` — all green.

---

## Batch 4: Kickoff Detection (Stage 3 Addition)

### Task 4.1: Add kickoff keyword scanning
- **File:** `pipeline/transcription.py` (add function)
- **What:**
  - `detect_kickoffs(utterances: list[dict]) -> dict` — scan for first-half and second-half kickoff keywords. Return `{"kickoff_first_half": float | None, "kickoff_second_half": float | None}`.
  - Keyword lists: `FIRST_HALF_KEYWORDS = ["kick off", "kicked off", "underway", "we're off", "here we go", "the match begins", "we are off"]`, `SECOND_HALF_KEYWORDS = ["second half", "second 45", "back underway", "restart", "second period"]`.
  - For each keyword match, record the utterance start time. Pick the earliest match for each half.
- **Test (write first):**
  - Sample utterances with "and we're underway" at 330s → detects 330.0 for first half.
  - Sample with "second half is underway" at 3420s → detects 3420.0.
  - No matches → returns None for both.
  - Multiple matches → picks earliest.

### Task 4.2: Audio energy corroboration
- **File:** `pipeline/transcription.py`
- **What:**
  - After finding keyword candidates, optionally verify with audio energy check. If energy near the candidate time is below baseline, reduce confidence or try next candidate.
  - This is a refinement — start simple (keyword-only), add energy check as a bonus.
- **Test:** Test with mocked energy data.

### Task 4.3: Integrate kickoff detection into `transcribe()`
- **File:** `pipeline/transcription.py`
- **What:**
  - After transcription completes, call `detect_kickoffs()`.
  - Add results to `transcription.json` output.
  - If cached transcription exists but lacks kickoff fields, re-detect from cached utterances.
- **Test:** Verify the transcription dict includes kickoff fields.

**Checkpoint:** Run `pytest`, `ruff`, `mypy` — all green.

---

## Batch 5: Event Aligner (Stage 4)

### Task 5.1: Create `pipeline/event_aligner.py` — mapping formula
- **File:** `pipeline/event_aligner.py` (new)
- **What:**
  - `EventAlignerError` exception class.
  - `estimate_video_timestamp(event: MatchEvent, kickoff_first: float, kickoff_second: float) -> float` — the pure math mapping.
  - Handle: 1st half, 2nd half, stoppage time, extra time.
- **Test (write first):**
  - 1st half goal at minute 21: `kickoff_first + 21*60`.
  - 2nd half goal at minute 83: `kickoff_second + (83-45)*60`.
  - Stoppage time 90+3: `kickoff_second + 45*60 + 3*60`.
  - Edge case: minute 45 (end of first half).

### Task 5.2: Audio-based timestamp refinement
- **File:** `pipeline/event_aligner.py`
- **What:**
  - `refine_timestamp(estimated_ts: float, utterances: list[dict], audio_path: Path) -> tuple[float, float]` — returns `(refined_ts, confidence)`.
  - Find utterances within ±60s of estimated_ts. Pick the one with highest energy. Snap to its start time.
  - Confidence = 0.9 if strong energy peak found, 0.5 if no clear peak, 0.3 if no utterances in window.
- **Test:**
  - Mock utterances with energy data. Verify it picks the highest-energy utterance.
  - No utterances in window → returns estimated_ts with low confidence.

### Task 5.3: Orchestrator — `align_events()`
- **File:** `pipeline/event_aligner.py`
- **What:**
  - `align_events(match_events: list[dict], transcription: dict, metadata: dict) -> list[dict]`
  - Load match_events.json and transcription.json from workspace.
  - For each event: estimate → refine → produce AlignedEvent.
  - Cache as `aligned_events.json`.
- **Test:** Full flow with mocked data. Verify output structure and caching.

**Checkpoint:** Run `pytest`, `ruff`, `mypy` — all green.

---

## Batch 6: Clip Builder (Stage 5)

### Task 6.1: Create `pipeline/clip_builder.py` — cut point calculation
- **File:** `pipeline/clip_builder.py` (new)
- **What:**
  - `ClipBuilderError` exception class.
  - `calculate_clip_windows(events: list[AlignedEvent], video_duration: float) -> list[dict]` — apply pre/post roll from `config/clip_windows.py`, clamp to video bounds.
- **Test (write first):**
  - Goal at 1000s → clip 985–1030 (15 pre, 30 post).
  - Event at 5s → clip 0–20 (clamped start).
  - Event at video_duration-3 → clamped end.

### Task 6.2: Clip merging
- **File:** `pipeline/clip_builder.py`
- **What:**
  - `merge_clips(clips: list[dict], gap_seconds: float) -> list[dict]` — merge overlapping/adjacent clips. Combine event descriptions.
- **Test:**
  - Two clips 5s apart → merged into one.
  - Two clips 30s apart → stay separate.
  - Three overlapping clips → one merged clip.

### Task 6.3: Budget enforcement
- **File:** `pipeline/clip_builder.py`
- **What:**
  - `enforce_budget(clips: list[dict], budget_seconds: float) -> list[dict]` — if total duration exceeds budget, drop lowest-priority events using `EVENT_PRIORITY` from clip_windows.
- **Test:**
  - 5 clips totaling 8 min with 5 min budget → drops lowest-priority clips.
  - Under budget → no change.

### Task 6.4: Orchestrator — `build_highlights()`
- **File:** `pipeline/clip_builder.py`
- **What:**
  - `build_highlights(aligned_events: list[dict], metadata: dict, overwrite: bool = False) -> dict`
  - Load aligned_events.json. Calculate windows → merge → enforce budget → cut clips (FFmpeg) → concat → write clip_manifest.json + highlights.mp4.
  - Cache-aware.
- **Test:** Mock FFmpeg calls. Verify clip_manifest.json output structure. Verify correct FFmpeg calls.

**Checkpoint:** Run `pytest`, `ruff`, `mypy` — all green.

---

## Batch 7: Interactive CLI

### Task 7.1: Rewrite `main.py`
- **File:** `main.py`
- **What:**
  - Interactive loop: prompt → match_finder → match_events → transcription → event_aligner → clip_builder.
  - Show progress at each stage.
  - Handle errors gracefully, allow retry.
  - Support URL fallback ("Enter a YouTube URL instead:").
  - Support fixture ID fallback if API-Football search fails.
- **No automated tests** for the interactive CLI itself (it's I/O-bound). Test the individual stages instead.

### Task 7.2: User confirmation prompts
- **File:** `main.py` (or `pipeline/match_finder.py`)
- **What:**
  - After YouTube search: "Found: <title> (<duration>). Proceed? [Y/n]"
  - After API-Football search: "Found: <team1> vs <team2>, <date>. Correct? [Y/n]"
  - On kickoff detection failure: "Couldn't auto-detect kickoff. Enter first half start time (e.g. 5:30):"

**Checkpoint:** Manual test with a real match. Run `pytest`, `ruff`, `mypy`.

---

## Batch 8: Integration & Polish

### Task 8.1: End-to-end integration test
- **File:** `tests/test_integration.py` (new)
- **What:** Feed known match data (mocked API + mocked transcription) through the full pipeline. Verify aligned_events.json timestamps and clip_manifest.json cut points are reasonable.

### Task 8.2: Update documentation
- **Files:** `CLAUDE.md`, `README.md`
- **What:** Update architecture diagram, stage table, environment variables, setup instructions.

### Task 8.3: Add `requests` to requirements.txt (if needed)
- **File:** `requirements.txt`
- **What:** Add `requests` for the API-Football HTTP client, if not already present.

### Task 8.4: Update `.env.example` or docs
- **What:** Document `API_FOOTBALL_KEY` alongside the existing keys.

**Checkpoint:** Full test suite green. Manual end-to-end run with a real match.

---

## Dependency Order

```
Batch 1 (foundation) → Batch 2 (match_events) → Batch 3 (match_finder)
                     → Batch 4 (kickoff detection)
                     → Batch 5 (event_aligner) [depends on Batch 2 + 4]
                     → Batch 6 (clip_builder) [depends on Batch 5]
                     → Batch 7 (CLI) [depends on all above]
                     → Batch 8 (integration)
```

Batches 2, 3, and 4 can run in parallel after Batch 1 is done.
