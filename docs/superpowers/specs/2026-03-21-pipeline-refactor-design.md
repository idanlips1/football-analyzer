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
- Supported query types (v1): full match summary, event-type filter, player-specific.
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
    ├── [interactive]        → confirm kickoffs → (first_ts, second_ts)
    ├── event_aligner.py     → align events using confirmed kickoffs → aligned_events.json
    └──                      → write game.json

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
| `match_finder.py` | Accepts `StorageBackend` instead of `PIPELINE_WORKSPACE` |
| `match_events.py` | Accepts `StorageBackend` |
| `transcription.py` | Accepts `StorageBackend` |
| `event_aligner.py` | Accepts `StorageBackend`; workspace path derived from it |
| `clip_builder.py` | Accepts `StorageBackend` + `confirm_overwrite_fn`; new signature (see below) |
| `main.py` | **Rewritten** as query REPL |
| `ingest.py` | **New** — ingest entrypoint |
| `pipeline/query_interpreter.py` | **New** |
| `pipeline/event_filter.py` | **New** |
| `models/game.py` | **New** |
| `models/highlight_query.py` | **New** |
| `utils/storage.py` | **New** |
| `utils/game_registry.py` | **New** |
| `tests/conftest.py` | Add `tmp_storage` fixture (see Testing section) |

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
    video_filename: str          # filename only, e.g. "match.mp4"
    source: str                  # canonical YouTube URL: "https://www.youtube.com/watch?v=<id>"
    duration_seconds: float
    kickoff_first_half: float    # seconds in video — hand-confirmed during ingest
    kickoff_second_half: float   # seconds in video — hand-confirmed during ingest
```

`is_ready` is **not** a stored field — it is computed by `GameRegistry` at load time
(both `game.json` and `aligned_events.json` must exist). It is not serialised to
`game.json`. The workspace directory is derived at runtime via
`storage.workspace_path(video_id)`.

`source` is always the canonical URL form. Regardless of whether the user entered a
URL or a text query, `ingest.py` normalises it as
`f"https://www.youtube.com/watch?v={video_id}"` before writing `game.json`.

### `models/highlight_query.py`

```python
class QueryType(StrEnum):
    FULL_SUMMARY = "full_summary"
    EVENT_FILTER = "event_filter"
    PLAYER       = "player"

@dataclass
class HighlightQuery:
    query_type: QueryType
    event_types: list[EventType] | None = None  # populated for EVENT_FILTER
    player_name: str | None = None              # populated for PLAYER
    raw_query: str = ""                         # original user text, kept for logging
```

### `utils/storage.py`

```python
class StorageBackend(Protocol):
    def read_json(self, video_id: str, filename: str) -> dict[str, Any]: ...
    def write_json(self, video_id: str, filename: str, data: dict[str, Any]) -> None: ...
    def local_path(self, video_id: str, filename: str) -> Path: ...
    # LOCAL: returns the real filesystem path.
    # AZURE: downloads the blob to a temp file and returns that path.
    # All callers (FFmpeg, clip_builder) always receive a real Path.
    def workspace_path(self, video_id: str) -> Path: ...
    # LOCAL: PIPELINE_WORKSPACE / video_id
    # AZURE: a temp directory scoped to the session
    def list_games(self) -> list[str]: ...
    # Returns video_ids where BOTH game.json AND aligned_events.json exist.
    # GameRegistry.list_ready() can safely call read_json for every returned ID.

class LocalStorage:
    def __init__(self, root: Path) -> None: ...
    # root defaults to PIPELINE_WORKSPACE from config/settings.py
```

### `utils/game_registry.py`

```python
class GameRegistry:
    def __init__(self, storage: StorageBackend) -> None: ...

    def list_ready(self) -> list[GameState]:
        # 1. Call storage.list_games() → list of video_ids
        #    (guaranteed: game.json + aligned_events.json both exist for each)
        # 2. For each video_id: storage.read_json(video_id, "game.json") → dict
        # 3. Deserialise to GameState (is_ready NOT stored; always inferred as True here)
        # 4. Return list[GameState]
        ...
```

### `pipeline/query_interpreter.py`

```python
class QueryInterpreterError(Exception): ...

def interpret_query(
    raw_query: str,
    game: GameState,
    aligned_events: list[AlignedEvent],
) -> HighlightQuery:
    ...
```

- Extracts unique player names and event types from `aligned_events` and injects them
  into the system prompt so the LLM can resolve e.g. "Mbappe" to an exact player
  string present in the data.
- Calls OpenAI (`OPENAI_API_KEY`, `OPENAI_MODEL`) with a structured prompt requesting
  a JSON response matching the `HighlightQuery` schema.
- On success: parses the JSON into a `HighlightQuery` and returns it.
- On LLM failure or unparseable response: logs a warning and returns
  `HighlightQuery(query_type=QueryType.FULL_SUMMARY, raw_query=raw_query)`.
- Raises `QueryInterpreterError` only on hard pre-call failures (missing API key).

### `pipeline/event_filter.py`

Pure function — no I/O, no external calls.

```python
def filter_events(
    events: list[AlignedEvent],
    query: HighlightQuery,
) -> list[AlignedEvent]:
    ...
```

| `query_type` | Filter logic |
|---|---|
| `FULL_SUMMARY` | Return all events unchanged |
| `EVENT_FILTER` | Keep only events whose `event_type` is in `query.event_types` |
| `PLAYER` | Keep events where `event.player` fuzzy-matches `query.player_name` |

**EVENT_FILTER guard:** if `query.event_types is None` (LLM failed to populate it),
treat as `FULL_SUMMARY` and log a warning. Do not raise — `None` is a valid sentinel
for "not applicable".

**PLAYER fuzzy matching:** uses `difflib.get_close_matches` (stdlib, no new
dependency). Match threshold: `cutoff=0.6`, `n=1`. If no match at 0.6, falls back to
a case-insensitive substring check before returning an empty list.

If the filtered result is empty for any reason, returns the full event list with a
printed warning rather than producing an empty highlights file.

---

## `clip_builder.py` Refactored Signature

```python
ConfirmOverwriteFn = Callable[[str], bool]  # arg: existing filepath str; return: overwrite?

def build_highlights(
    events: list[AlignedEvent],
    game: GameState,
    query: HighlightQuery,
    storage: StorageBackend,
    *,
    confirm_overwrite_fn: ConfirmOverwriteFn = _interactive_confirm_overwrite,
) -> dict[str, Any]:
    ...
```

Inside `build_highlights`:
1. Compute `slug = _query_slug(query)`.
2. Derive output path: `storage.workspace_path(game.video_id) / f"highlights_{slug}.mp4"`.
3. If the file exists: call `confirm_overwrite_fn(str(output_path))`. If `False`, return
   immediately with the cached result (same shape as current cache-hit path).
4. Proceed with clip window calculation → merge → budget → FFmpeg cut → concat → write.
5. Return `{"highlights_path": str(output_path), "clip_count": ..., ...}`.

```python
def _query_slug(query: HighlightQuery) -> str:
    base = query.raw_query.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", base).strip("_")[:40]
    return slug or query.query_type.value
```

`_query_slug` lives in `pipeline/clip_builder.py`. This requires importing
`HighlightQuery` from `models/highlight_query.py` into `clip_builder.py`, which is
a valid and intentional dependency.

---

## Kickoff Confirmation Type

```python
ConfirmKickoffsFn = Callable[[float | None, float | None], tuple[float, float]]
# Args: (auto-detected first-half ts or None, auto-detected second-half ts or None)
# Returns: (confirmed first-half ts, confirmed second-half ts)
```

Default interactive implementation behaviour:
- If a value is detected: print `"  First half kickoff detected at 5:32 — correct? [Y/n]"`.
  If confirmed: return it. If rejected: prompt for manual entry.
- If a value is `None`: print `"  Could not detect second half kickoff."` then prompt
  `"  Enter second half kickoff time (mm:ss or seconds): "` and parse the input.
  Repeated until a valid float is entered (no exit path — a kickoff time is required).

---

## Storage Layout

```
pipeline_workspace/
└── <video_id>/
    ├── game.json              ← GameState (no is_ready field; derived at load time)
    ├── metadata.json          ← kept for backwards compat (not read by new code)
    ├── match_events.json
    ├── transcription.json
    ├── aligned_events.json
    ├── audio.wav
    ├── <video_filename>.mp4
    ├── clip_manifest.json
    ├── clips/
    └── highlights_<slug>.mp4  ← one per query; multiple can coexist
```

`StorageBackend.list_games()` returns only video_ids where **both** `game.json` and
`aligned_events.json` exist. Partially-ingested games are invisible to the query REPL.

**Partial ingest / re-runs:** If a crash occurs after `aligned_events.json` is written
but before `game.json` is written, the game won't appear in `list_games()`. A re-run
skips all cached stages and resumes from kickoff confirmation, then writes `game.json`.
No manual cleanup is required.

---

## `ingest.py` Flow

```
python ingest.py
```

1. Prompt: YouTube URL or text search query.
2. If text query: search YouTube, let user pick a video, resolve to URL. In both
   cases, extract `video_id` and normalise `source = f"https://www.youtube.com/watch?v={video_id}"`.
3. Download video via injected `StorageBackend` (default: `LocalStorage`).
4. Resolve fixture ID from video title.
5. Fetch API-Football events → `match_events.json`.
6. Run AssemblyAI transcription → `transcription.json`.
   Transcription produces auto-detected `kickoff_first_half` and `kickoff_second_half`
   (may be `None` if detection fails).
7. Call `confirm_kickoffs_fn(auto_first, auto_second)` → `(first_ts, second_ts)`.
   Kickoffs are confirmed **before** alignment so that alignment uses accurate timestamps.
8. Run event alignment using confirmed `(first_ts, second_ts)` → `aligned_events.json`.
9. Write `game.json` via `StorageBackend` (only after alignment completes successfully).
10. Print: `Game ingested — ready for queries.`

---

## `main.py` Query REPL Flow

```
python main.py
```

1. `GameRegistry(LocalStorage(...))` → `list_ready()` → `list[GameState]`.
2. Show numbered game list.
3. User picks a game by number.
4. Load `aligned_events.json` → deserialise to `list[AlignedEvent]`.
5. Inner REPL:
   ```
   > What highlights do you want?
   > show me all the goals and penalties
   Understood: event filter — goal, own_goal, penalty
   [5/5] Cutting clips...
   Done! highlights_show_me_all_the_goals_and_penal.mp4 — 3 clips | 2m15s
   > another highlights reel for this game? [Y/n]
   ```
6. `interpret_query(raw, game, aligned_events)` → `HighlightQuery`.
7. `filter_events(aligned_events, query)` → filtered `list[AlignedEvent]`.
8. `build_highlights(events, game, query, storage, confirm_overwrite_fn=...)`.
9. User can type `back` (return to game list) or `quit` (exit).

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Ingest stage fails | Exception caught, printed; `game.json` not written; safe to re-run |
| Kickoff auto-detection fails | `confirm_kickoffs_fn` prompts for manual entry (required; no skip) |
| OpenAI LLM failure | Warning logged; falls back to `FULL_SUMMARY` |
| Filtered event list is empty | Warning printed; falls back to full event list |
| No games ready | Helpful message + exit cleanly |
| Highlights slug collision | `confirm_overwrite_fn(path)` called |

---

## Testing

| What | How |
|---|---|
| `event_filter.py` | Pure unit tests — fixture `AlignedEvent` lists, no mocks |
| `query_interpreter.py` | Mock OpenAI HTTP; valid JSON → correct `HighlightQuery`; malformed → FULL_SUMMARY fallback |
| `GameRegistry` / `LocalStorage` | New `tmp_storage` fixture (see below) |
| `ingest.py` end-to-end | Mock yt-dlp, AssemblyAI, HTTP; inject `confirm_kickoffs_fn` stub |
| `clip_builder.py` (new tests) | Rewrite `test_clip_builder.py` to use new signature: pass `list[AlignedEvent]`, `GameState`, `HighlightQuery`, `LocalStorage(tmp_path)`, and inject `confirm_overwrite_fn` stub returning `True`. |
| Legacy pipeline module tests | Continue using `tmp_workspace` until each module is migrated to `StorageBackend`. `tmp_workspace` must remove the `"pipeline.clip_builder.PIPELINE_WORKSPACE"` patch entry when `clip_builder.py` is migrated. |

**`tmp_storage` fixture** (added to `conftest.py`):
```python
@pytest.fixture
def tmp_storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(root=tmp_path)
```

**`tmp_workspace` migration:** The existing `tmp_workspace` fixture patches
`PIPELINE_WORKSPACE` individually on each module. As each module is migrated to accept
`StorageBackend`, its patch entry is removed from `tmp_workspace`. When all modules are
migrated, `tmp_workspace` is removed entirely. This migration is incremental and
module-by-module — no big-bang fixture rewrite needed.

---

## Out of Scope (v1)

- Azure Blob Storage backend (protocol designed for it; not implemented)
- Time-window query type (e.g. "last 15 minutes" or "second half only")
- Team-specific highlights
- Web UI
- Multi-user game sharing
