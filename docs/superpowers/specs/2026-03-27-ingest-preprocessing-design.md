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
  5. [NEW] Write game.json (includes fixture_id from step 4 if selected)
  6. [NEW] Fetch all match events → match_events.json
  7. [NEW] Align all events → aligned_events.json

QUERY (per user request, fast)
  1. Load game.json + aligned_events.json from storage
  2. Extract player names from aligned events
  3. LLM: interpret query (OpenAI) → HighlightQuery
  4. Local filter: filter_events(aligned_events, query)
  5. Build clips → highlights.mp4
```

## Section 1: Schema Changes

### `CatalogMatch.events_snapshot`

Two changes required:

1. **Dataclass field annotation** (`catalog/loader.py` line 23): change `events_snapshot: str` to `events_snapshot: str | None`.
2. **Loader read expression** (`catalog/loader.py` line 45): change `row["events_snapshot"]` to `row.get("events_snapshot") or None`.

All existing catalog entries in `matches.json` supply a non-empty string for `events_snapshot`, so this change is backward-compatible.

Skip condition for Step 4 (fixture selection): `if not entry.events_snapshot` — run Step 4 only when `events_snapshot` is `None` or `""`.

**`merge_catalog_metadata` in `catalog_pipeline.py`** does `meta["events_snapshot"] = entry.events_snapshot` unconditionally. When `entry.events_snapshot` is `None`, this writes `None` into the metadata dict. `fetch_match_events` reads `snapshot_key = metadata.get("events_snapshot")` and treats a falsy value as "no snapshot" — so `None` propagates correctly. No change to `merge_catalog_metadata` is required.

### `GameState.fixture_id`

Change `fixture_id: int` to `fixture_id: int | None` in `models/game.py`.

The existing construction in `ingest.py` — `fixture_id=int(metadata.get("fixture_id") or 0)` — must be changed to:

```python
fixture_id=int(fid) if (fid := metadata.get("fixture_id")) is not None else None
```

This prevents silent write of `0` for matches that have no fixture.

After this type change, run `mypy` to verify no remaining call site passes `fixture_id` as a required non-optional `int`. The deleted `_get_players_map` call in `query_interpreter.py` (line 153) passes `game.fixture_id` directly — deleting that function removes the only known hazard.

## Section 2: Ingest Changes

### Step ordering in `_run_catalog_ingest` (ingest.py)

The existing three steps stay; four new steps are added. Total becomes 7. The CLI progress strings update from `[1/3]`…`[3/3]` to `[1/7]`…`[7/7]`.

**Step 4 — Fixture selection (conditional on `not entry.events_snapshot`)**

Only runs when the catalog entry has no non-empty `events_snapshot`.

API strategy (two-step):
1. Call `/teams?search={team_name}` for each team to resolve names to API-Football team IDs. Match the first result whose name is a case-insensitive substring match or difflib close match (cutoff ≥ 0.6). If either team fails to resolve, print an error and abort.
2. Call `/fixtures/headtohead?h2h={home_id}-{away_id}&season={year}`. `{year}` is derived by taking the first 4-character numeric token from `season_label` (e.g. `"2018-19"` → `"2018"`, `"2022"` → `"2022"`).

Operator selection:
- 0 results: print error, abort ingest
- 1 result: display date, teams, competition, fixture ID; ask "Use this fixture? [Y/n]". On "n", abort.
- 2+ results: show numbered list (date, teams, competition, fixture ID); operator picks by number or "q" to abort.

Selected `fixture_id` is held in memory and passed to Step 5.

**Step 5 — Write game.json**

`game.json` is written here. This replaces the current write at the end of the kickoff-confirmation block (current `ingest.py` ~line 180). The `GameState` is constructed with:
- `fixture_id` from Step 4 if selected, otherwise `None` (not `0`)
- kickoffs confirmed in Step 3

This is the only write of `game.json` during ingest.

**Step 6 — Fetch events**

Call `fetch_match_events(metadata, storage)` → writes `match_events.json`.

- Snapshot matches (`events_snapshot` non-empty): copies snapshot JSON, no network call
- `fixture_id` matches: calls API-Football `/fixtures/events`
- Already handles caching (skips if `match_events.json` exists)

**Step 7 — Align events**

Call:
```python
align_events(events_data, metadata, storage, k_first, k_second, force_recompute=False, save_to_disk=True)
```

No changes to the `align_events` signature — it already takes `match_events_data`, `metadata`, `storage`, `kickoff_first`, `kickoff_second` as positional args and reads `transcription.json` internally from storage. Use `force_recompute=False` to respect the cache on re-runs (consistent with all other caching steps). Use `save_to_disk=True` (the default) to persist `aligned_events.json`.

### New helper: `_pick_fixture_interactive`

```python
def _pick_fixture_interactive(
    home_team: str,
    away_team: str,
    season_label: str,
) -> int | None:
    """Search API-Football for fixtures and let operator pick. Returns fixture_id or None."""
```

Lives in `ingest.py`. Returns `None` if the operator quits or no results are found.

## Section 3: Query-Time Changes

### `run_catalog_pipeline` (catalog_pipeline.py)

Simplified flow:

```python
# Load pre-processed data
game = GameState.from_dict(storage.read_json(match_id, "game.json"))
try:
    aligned_data = storage.read_json(match_id, "aligned_events.json")
except Exception:
    raise CatalogPipelineError(
        f"Missing aligned events for {match_id}. Run ingest.py first."
    )

aligned_events = [AlignedEvent.from_dict(e) for e in aligned_data.get("events", [])]
if not aligned_events:
    raise CatalogPipelineError(
        f"aligned_events.json for {match_id} is empty or stale. Re-run ingest.py."
    )

# Extract player names from pre-aligned events (no API call)
player_names = sorted({
    name
    for e in aligned_events
    for name in [e.player, e.assist]
    if name
})

# LLM interpretation only (falls back to FULL_SUMMARY on failure)
hq = interpret_query(highlights_query, game, player_names)

# Local filtering
filtered = filter_events(aligned_events, hq)

# Build clips
result = build_highlights(filtered, game, hq, storage, ...)
```

The existing fallback in the broad `except Exception` block (line ~93) that calls `interpret_query(highlights_query, game, [])` with an empty list remains valid after the signature change — `[]` satisfies `list[str]` and is intentional.

**Removed from query time:**
- `fetch_filtered_events` call and import
- `_get_players_map` call
- `align_events` call
- `kickoff_first_override` and `kickoff_second_override` parameters

**Progress callback stages** updated: remove `"fetching_dynamic_events"` and `"aligning"`; add `"loading_events"`, `"filtering"`, `"building_clips"`. Update in `catalog_pipeline.py`, `worker/runner.py` (three layers: `_run_pipeline` signature, `process_job` signature, queue-message extraction block), and any downstream API consumers (webhook payloads, frontend).

**Dead kickoff parameters:** Remove `kickoff_first_override` and `kickoff_second_override` from `run_catalog_pipeline` signature and from all call sites in `worker/runner.py`. The queue message schema changes accordingly: `kickoff_first_half` / `kickoff_second_half` fields sent by the API job-creation layer become dead. Review `api/routes/jobs.py` (or equivalent) to stop sending them.

### `interpret_query` signature change (query_interpreter.py)

```python
# Before
def interpret_query(
    raw_query: str,
    game: GameState,
    aligned_events: list[AlignedEvent],
) -> HighlightQuery:

# After
def interpret_query(
    raw_query: str,
    game: GameState,
    player_names: list[str],
) -> HighlightQuery:
```

`player_names` is passed directly by the caller. No internal API call.

### `local_run.py` updates

`local_run.py` is a development convenience runner that currently mirrors the old query-time flow. After this change it should mirror the new one:

1. Remove the `--use-cached-events` CLI flag entirely — in the new flow there is no "fetch events" stage in `local_run.py`; events always come from pre-ingested `aligned_events.json`.
2. Load `aligned_events.json` from storage (replacing stages 2 and 3 in the current `[1/5]`…`[5/5]` flow). Update stage count in print strings accordingly.
3. Remove `fetch_filtered_events` import and call.
4. Remove `align_events` import and call.
5. Update `interpret_query` call to pass `player_names` extracted from loaded `aligned_events`.
6. Remove the `api_event_type={hq.api_event_type}` print on line 127 — this field is deleted from `HighlightQuery` and will raise `AttributeError`.

## Deleted Code

| Item | Location |
|---|---|
| `fetch_filtered_events` | `pipeline/match_events.py` |
| `_get_players_map` | `pipeline/query_interpreter.py` |
| `_EVENTTYPE_TO_API_TYPE` dict | `pipeline/query_interpreter.py` |
| derivation of `api_event_type` and `api_player_id` in `interpret_query` | `pipeline/query_interpreter.py` |
| `api_player_id` field | `models/highlight_query.py` |
| `api_team_id` field | `models/highlight_query.py` |
| `api_event_type` field | `models/highlight_query.py` |
| `aligned_events: list[AlignedEvent]` param from `interpret_query` | `pipeline/query_interpreter.py` |
| `kickoff_first_override` param | `pipeline/catalog_pipeline.py` |
| `kickoff_second_override` param | `pipeline/catalog_pipeline.py` |
| `--use-cached-events` CLI flag | `local_run.py` |
| `api_event_type` print line (~line 127) | `local_run.py` |

## Data Flow Summary

| Artifact | Written by | Read by |
|---|---|---|
| `metadata.json` | ingest step 1 | ingest steps 6–7, query |
| `transcription.json` | ingest step 2 | ingest step 7 (read internally from storage by `align_events`) |
| `game.json` | ingest step 5 (after fixture selection in step 4) | query |
| `match_events.json` | ingest step 6 | ingest step 7 |
| `aligned_events.json` | ingest step 7 | query |

Note: `game.json` is written once, in step 5, after `fixture_id` is resolved. The current write at the end of step 3 is removed.

## Error Handling

| Condition | Error raised | Message |
|---|---|---|
| `aligned_events.json` missing at query time | `CatalogPipelineError` | `"Missing aligned events for {match_id}. Run ingest.py first."` |
| `aligned_events.json` present but empty | `CatalogPipelineError` | `"aligned_events.json for {match_id} is empty or stale. Re-run ingest.py."` |
| Zero fixtures found during Step 4 | print + abort | no exception propagated |
| Team name resolution fails during Step 4 | print + abort | no exception propagated |
| Fixture selection cancelled by operator | abort | no exception propagated |
| Stale but non-empty `aligned_events.json` | not auto-detected | operator deletes cached files and re-runs ingest |

## Testing

- `test_ingest.py`:
  - Mock `fetch_match_events` and `align_events`; assert they are called with correct args after kickoff confirmation; assert `force_recompute=False` and `save_to_disk=True` are passed to `align_events`
  - Assert `aligned_events.json` is written via `storage.write_json`
  - **Snapshot-present path**: assert `_pick_fixture_interactive` is NOT called; assert `fetch_match_events` IS called; assert `fixture_id` written to `game.json` is `None`
  - **Fixture selection — 0 results**: mock API returns empty list; assert ingest aborts without writing `game.json`
  - **Fixture selection — 1 result**: mock confirm prompt; assert selected `fixture_id` written to `game.json`
  - **Fixture selection — 2+ results**: mock numbered list; test valid pick and operator cancel
- `test_catalog_pipeline.py`:
  - Mock `storage.read_json` to return pre-baked `aligned_events.json`
  - Assert no `fetch_filtered_events`, `_get_players_map`, or `align_events` calls are made
  - Assert `interpret_query` receives `player_names: list[str]`
  - Test missing `aligned_events.json` raises `CatalogPipelineError` with correct message
  - Test empty `aligned_events.json` raises `CatalogPipelineError` with correct message
- `test_query_interpreter.py`:
  - Update all call sites to pass `player_names: list[str]`
  - Assert no internal API call is made
