# Design: Move API-Football + Alignment to Ingest (Preprocessing)

**Date:** 2026-03-27

## Problem

The current `run_catalog_pipeline` (query time) makes two API-Football calls and runs full event alignment on every user query:

1. `_get_players_map` — calls `/fixtures/lineups` to build a player name → ID map for the LLM prompt
2. `fetch_filtered_events` — calls `/fixtures/events` with query-specific filters
3. `align_events` with `force_recompute=True, save_to_disk=False` — recomputes alignment every query

This is wrong for two reasons:
- API calls and heavy computation belong in the preprocessing (ingest) stage, not query time
- `fetch_filtered_events` raises `MatchEventsError` for all current catalog matches (which have `fixture_id: null`), making the query pipeline broken for the entire catalog

**The only external call at query time should be the LLM (query interpretation + label generation).**

## Desired Pipeline Boundaries

```
INGEST (once per match, operator-run)
  1. Download / upload video
  2. Transcription (AssemblyAI)
  3. Confirm kickoff timestamps
  4. [NEW] Fixture selection (if no snapshot: search API-Football, operator picks)
  5. [NEW] Fetch all match events → match_events.json
  6. [NEW] Align all events → aligned_events.json

QUERY (per user request, fast)
  1. Load game.json + aligned_events.json from storage
  2. Extract player names from aligned events
  3. LLM: interpret query (OpenAI) → HighlightQuery
  4. Local filter: filter_events(aligned_events, query)
  5. Build clips → highlights.mp4
```

## Section 1: Ingest Changes

### New steps in `_run_catalog_ingest` (ingest.py)

After step 3 (kickoff confirmation), add:

**Step 4 — Fixture selection (conditional)**

Only runs when the catalog entry has neither `events_snapshot` nor `fixture_id`.

- Search API-Football `/fixtures` by home team, away team, and season year
- If 0 results: print error, abort ingest
- If 1 result: show match details, ask operator to confirm (Y/n)
- If 2+ results: show numbered list (date, teams, competition, fixture ID), operator picks by number
- Selected `fixture_id` is written into `game.json`

For entries with an `events_snapshot` key, this step is skipped entirely.

**Step 5 — Fetch events**

Call `fetch_match_events(metadata, storage)` → writes `match_events.json`.

- For snapshot matches: copies snapshot JSON (fast, no network)
- For `fixture_id` matches: calls API-Football `/fixtures/events`
- Already handles caching (skips if `match_events.json` exists)

**Step 6 — Align events**

Call `align_events(events_data, metadata, storage, k_first, k_second)` with default `save_to_disk=True` → writes `aligned_events.json`.

Uses transcription utterances and confirmed kickoff timestamps from earlier steps.

### New helper: `_pick_fixture_interactive`

```python
def _pick_fixture_interactive(home_team: str, away_team: str, season_label: str) -> int | None:
    """Search API-Football for fixtures matching the teams/season and let operator pick."""
```

- Calls `/fixtures/headtohead?h2h={home_id}-{away_id}` or `/fixtures?team=...&season=...`
- Displays candidates as a numbered list
- Returns selected `fixture_id` or `None` if operator quits

## Section 2: Query-Time Changes

### `run_catalog_pipeline` (catalog_pipeline.py)

Simplified flow:

```python
# Load pre-processed data
game = GameState.from_dict(storage.read_json(match_id, "game.json"))
aligned_data = storage.read_json(match_id, "aligned_events.json")
aligned_events = [AlignedEvent.from_dict(e) for e in aligned_data.get("events", [])]

# Extract player names from pre-aligned events (no API call)
player_names = sorted({
    name
    for e in aligned_events
    for name in [e.player, e.assist]
    if name
})

# LLM interpretation only
hq = interpret_query(highlights_query, game, player_names)

# Local filtering
filtered = filter_events(aligned_events, hq)

# Build clips
result = build_highlights(filtered, game, hq, storage, ...)
```

**Removed from query time:**
- `fetch_filtered_events` (deleted)
- `_get_players_map` (deleted from `query_interpreter.py`)

**Error if `aligned_events.json` missing:** clear message telling operator to run `ingest.py` first.

### `interpret_query` signature change

```python
# Before
def interpret_query(raw_query: str, game: GameState, aligned_events: list[AlignedEvent]) -> HighlightQuery:

# After
def interpret_query(raw_query: str, game: GameState, player_names: list[str]) -> HighlightQuery:
```

`player_names` is passed directly (already extracted by the caller). No internal API call.

The `api_player_id` field on `HighlightQuery` is removed (was used to filter API requests, no longer needed). Player matching at filter time uses fuzzy name matching in `filter_events` as it does today.

## Deleted Code

- `fetch_filtered_events` in `pipeline/match_events.py`
- `_get_players_map` in `pipeline/query_interpreter.py`
- `api_player_id` field from `HighlightQuery` and its derivation in `interpret_query`
- `api_event_type` field from `HighlightQuery` (was used only for API-side filtering)
- The `aligned_events: list[AlignedEvent]` parameter from `interpret_query` (replaced by `player_names`)

## Data Flow Summary

| Artifact | Written by | Read by |
|---|---|---|
| `metadata.json` | ingest step 1 | ingest steps 4–6, query |
| `transcription.json` | ingest step 2 | ingest step 6 (alignment) |
| `game.json` | ingest step 3 (+ fixture_id from step 4) | query |
| `match_events.json` | ingest step 5 | ingest step 6 |
| `aligned_events.json` | ingest step 6 | query |

## Error Handling

- Missing `aligned_events.json` at query time → `CatalogPipelineError` with message: `"Missing aligned events for {match_id}. Run ingestion first (ingest.py)."`
- Zero fixtures found during ingest → print error, abort gracefully (no exception propagation)
- Fixture selection cancelled by operator → abort ingest gracefully

## Testing

- `test_ingest.py`: mock `fetch_match_events` and `align_events`; assert they are called with correct args after kickoff confirmation; assert `aligned_events.json` written
- `test_catalog_pipeline.py`: mock `storage.read_json` to return pre-baked `aligned_events.json`; assert no API-Football calls made; assert `interpret_query` receives `player_names` list
- `test_query_interpreter.py`: update to pass `player_names: list[str]` instead of `aligned_events`; assert no `_get_players_map` call
- `test_fixture_selection.py`: mock API-Football responses; test 0/1/many results; test operator cancel
