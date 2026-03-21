# Pipeline Refactor: Preprocessing + Natural Language Query

**Date:** 2026-03-21
**Status:** Approved

---

## Summary

Refactor the football highlights generator from a single end-to-end pipeline into two
distinct stages:

1. **Ingest** (`ingest.py`) — one-time preprocessing per game: download, API events,
   transcription, kickoff labeling, persist to local storage.
2. **Query** (`main.py`) — on-demand REPL: user picks a preprocessed game, enters a
   natural language request, an LLM interprets it, events are filtered, and FFmpeg
   cuts a new highlights reel.

The storage layer is abstracted so local filesystem can be swapped for Azure Blob
Storage in a future iteration without touching any pipeline logic.

---

## Goals

- Video download, API-Football event fetch, and transcription run **once** per game.
- Kickoff timestamps are **hand-confirmed** during ingest (auto-detection remains as a
  suggestion; the operator confirms or overrides interactively).
- Users can generate **multiple different highlight reels** from one preprocessed game.
- Supported query types (v1): full match summary, event-type filter (e.g. "just the
  goals"), player-specific highlights.
- Architecture supports adding new query types with minimal change (extend
  `HighlightQuery`, add a branch in `event_filter.py`).
- Storage backend is injectable — local today, Azure-ready tomorrow.

---

## Architecture Overview

```
ingest.py  (one-time per game)
    ├── match_finder.py      → download video
    ├── match_events.py      → fetch API-Football events → match_events.json
    ├── transcription.py     → AssemblyAI transcription → transcription.json
    ├── event_aligner.py     → align events to video → aligned_events.json
    └── [interactive]        → confirm kickoffs → write game.json

main.py  (query REPL)
    ├── GameRegistry         → scan storage, load GameState per game
    ├── [user picks game + types natural language query]
    ├── query_interpreter.py → OpenAI → HighlightQuery
    ├── event_filter.py      → filter aligned_events by HighlightQuery
    └── clip_builder.py      → cut & concat → highlights_<slug>.mp4
```

### What changes vs. what stays

| Module | Change |
|--------|--------|
| `match_finder.py` | Minimal — accepts `StorageBackend` instead of raw `PIPELINE_WORKSPACE` |
| `match_events.py` | Minimal — same |
| `transcription.py` | Minimal — same |
| `event_aligner.py` | Minimal — same |
| `clip_builder.py` | Minimal — output filename uses query slug |
| `main.py` | **Rewritten** as query REPL |
| `ingest.py` | **New** — ingest entrypoint |
| `pipeline/query_interpreter.py` | **New** |
| `pipeline/event_filter.py` | **New** |
| `models/game.py` | **New** |
| `utils/storage.py` | **New** |

---

## New Files

### `models/game.py`

```python
@dataclass
class GameState:
    video_id: str
    home_team: str
    away_team: str
    league: str
    date: str                    # "YYYY-MM-DD"
    fixture_id: int
    video_filename: str
    duration_seconds: float
    kickoff_first_half: float    # seconds in video — hand-confirmed during ingest
    kickoff_second_half: float   # seconds in video — hand-confirmed during ingest
    is_ready: bool               # True when aligned_events.json also exists
```

### `models/highlight_query.py` (or in `models/game.py`)

```python
class QueryType(StrEnum):
    FULL_SUMMARY = "full_summary"
    EVENT_FILTER = "event_filter"
    PLAYER       = "player"

@dataclass
class HighlightQuery:
    query_type: QueryType
    event_types: list[EventType] | None  # populated for EVENT_FILTER
    player_name: str | None              # populated for PLAYER
    raw_query: str                       # original user text, kept for logging
```

### `utils/storage.py`

A `StorageBackend` Protocol with a `LocalStorage` implementation.
Pipeline modules accept a `StorageBackend` instead of constructing paths from
`PIPELINE_WORKSPACE` directly.

```python
class StorageBackend(Protocol):
    def read_json(self, video_id: str, filename: str) -> dict: ...
    def write_json(self, video_id: str, filename: str, data: dict) -> None: ...
    def video_path(self, video_id: str, filename: str) -> Path: ...
    def list_games(self) -> list[str]: ...  # returns video_ids with game.json + aligned_events.json

class LocalStorage:
    root: Path  # = PIPELINE_WORKSPACE
```

### `pipeline/query_interpreter.py`

- Calls OpenAI (`OPENAI_API_KEY`) with a structured system prompt and the user's
  natural language query.
- The prompt includes the game's available event types and player names (extracted
  from `aligned_events.json`) so the LLM can resolve "Mbappe" to an exact player
  name present in the data.
- Returns a `HighlightQuery` dataclass.
- On LLM failure or unparseable response: logs a warning, falls back to
  `QueryType.FULL_SUMMARY` so the user always gets output.
- Raises `QueryInterpreterError` only on hard failures (e.g. missing API key).

### `pipeline/event_filter.py`

Pure function — no I/O, no LLM:

```python
def filter_events(
    events: list[AlignedEvent],
    query: HighlightQuery,
) -> list[AlignedEvent]:
    ...
```

| `query_type` | Filter logic |
|---|---|
| `FULL_SUMMARY` | Return all events (substitutions already excluded by aligner) |
| `EVENT_FILTER` | Keep only events whose `event_type` is in `query.event_types` |
| `PLAYER` | Keep events where `event.player` fuzzy-matches `query.player_name` |

If the filtered result is empty, returns the full event list with a printed warning
rather than producing an empty highlights file.

---

## Storage Layout

```
pipeline_workspace/
└── <video_id>/
    ├── game.json              ← NEW: GameState (teams, date, kickoffs, fixture_id)
    ├── metadata.json          ← kept for backwards compat
    ├── match_events.json
    ├── transcription.json
    ├── aligned_events.json
    ├── audio.wav
    ├── <video>.mp4
    ├── clip_manifest.json
    ├── clips/
    └── highlights_<query-slug>.mp4   ← NEW naming (one file per query)
```

`StorageBackend.list_games()` returns only video_ids where **both** `game.json` and
`aligned_events.json` exist. Partially-ingested games are invisible to the query REPL.

---

## `ingest.py` Flow

```
python ingest.py
```

1. Prompt: YouTube URL or text search query.
2. Download video via `match_finder.py`.
3. Resolve fixture ID from video title (`resolve_fixture_for_video`).
4. Fetch API-Football events → `match_events.json`.
5. Run AssemblyAI transcription → `transcription.json`.
6. Run event alignment → `aligned_events.json`.
7. **Kickoff confirmation (interactive):**
   - Show auto-detected first-half kickoff: `Detected: 5:32 — correct? [Y/n]`
   - If rejected or not detected: prompt for manual entry (`mm:ss` or seconds).
   - Repeat for second-half kickoff.
8. Write `game.json` with all `GameState` fields.
9. Print: `Game ingested ✓ — ready for queries.`

---

## `main.py` Query REPL Flow

```
python main.py
```

1. `GameRegistry` (backed by `LocalStorage`) scans `pipeline_workspace/`, loads all
   ready `GameState` objects.
2. Display numbered list:
   ```
   [1] Real Madrid vs Barcelona  |  La Liga        |  2024-10-26
   [2] Arsenal vs PSG            |  Champions League|  2025-04-15
   ```
3. User picks a game by number.
4. Inner REPL for that game:
   ```
   > What highlights do you want?
   > show me all the goals and penalties
   Understood: event filter — goal, own_goal, penalty
   [5/5] Cutting clips...
   Done! highlights_goals-penalties.mp4 — 3 clips | 2m15s
   > another highlights reel? [Y/n]
   ```
5. `query_interpreter.py` → `HighlightQuery`.
6. `event_filter.py` → filtered `AlignedEvent` list.
7. `clip_builder.py` → `highlights_<slug>.mp4`.
8. User can type `back` to return to game selection, `quit` to exit.

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Ingest stage fails (download, API, transcription) | Exception caught, printed clearly; `game.json` not written; safe to re-run |
| Kickoff auto-detection fails | Interactive fallback prompt (same as current) |
| OpenAI LLM failure | Warning printed; falls back to `FULL_SUMMARY` |
| Filtered event list is empty | Warning printed; falls back to full event list |
| No games ready (empty workspace) | Print helpful message and exit |

---

## Testing

| What | How |
|---|---|
| `event_filter.py` | Pure unit tests — no mocks, fixture `AlignedEvent` lists |
| `query_interpreter.py` | Mock OpenAI HTTP; test valid JSON → correct `HighlightQuery`; malformed → fallback |
| `GameRegistry` / `LocalStorage` | Use existing `tmp_workspace` monkeypatch from `conftest.py` |
| `ingest.py` end-to-end | Mock yt-dlp, AssemblyAI, API-Football HTTP (existing patterns) |
| Existing pipeline tests | No changes needed |

---

## Out of Scope (v1)

- Azure Blob Storage backend (interface designed for it, not implemented)
- Time-window query type (e.g. "last 15 minutes" or "second half only")
- Team-specific highlights
- Web UI
- Multi-user game sharing
