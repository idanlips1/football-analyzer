# Ingest Preprocessing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move all API-Football calls and event alignment out of query time and into the ingest stage so the only external call during a user query is the LLM.

**Architecture:** Schema changes make `fixture_id` and `events_snapshot` nullable. Ingest gains four new steps: fixture selection (API-Football search + operator pick), game.json write, event fetch, and event alignment. Query time simplifies to: load pre-aligned events → extract player names → LLM interpret → local filter → build clips.

**Tech Stack:** Python 3.12, pytest + monkeypatch/unittest.mock, existing `pipeline/match_events.py` + `pipeline/event_aligner.py` (no signature changes needed), API-Football `/teams` + `/fixtures/headtohead` endpoints.

**Spec:** `docs/superpowers/specs/2026-03-27-ingest-preprocessing-design.md`

---

## File Map

| File | Change |
|---|---|
| `catalog/loader.py` | `events_snapshot: str` → `str | None`; use `row.get(...)` in loader |
| `models/game.py` | `fixture_id: int` → `int | None` |
| `models/highlight_query.py` | Remove `api_player_id`, `api_team_id`, `api_event_type` fields |
| `pipeline/query_interpreter.py` | Remove `_get_players_map`, `_EVENTTYPE_TO_API_TYPE`; change signature to `player_names: list[str]` |
| `pipeline/match_events.py` | Delete `fetch_filtered_events` |
| `pipeline/catalog_pipeline.py` | Load `aligned_events.json`; extract player names; remove kickoff overrides; update progress callbacks |
| `worker/runner.py` | Remove kickoff override params from all 3 layers |
| `api/schemas.py` | Remove `kickoff_first_half`/`kickoff_second_half` from `JobCreateRequest` |
| `api/routes/jobs.py` | Remove kickoff fields from queue message; fix cache-bypass condition |
| `ingest.py` | Move game.json write to step 5; add steps 6–7 (fetch events, align events); add `_pick_fixture_interactive` (step 4) |
| `local_run.py` | Remove `--use-cached-events`; load `aligned_events.json`; remove fetch/align stages; update `interpret_query` call |
| `tests/test_query_interpreter.py` | Update all call sites to `player_names: list[str]` |
| `tests/test_ingest.py` | Update existing tests; add tests for new steps 4–7 |
| `tests/test_catalog_pipeline.py` | Create new — test query-time simplification |
| `tests/test_worker.py` | Remove kickoff override params from `process_job` calls |
| `tests/test_api_jobs.py` | Remove kickoff fields from any test that sends them |

---

## Task 1: Make `events_snapshot` and `fixture_id` nullable

**Files:**
- Modify: `catalog/loader.py:14-48`
- Modify: `models/game.py:11-30`
- Modify: `tests/test_models.py` (any `GameState` construction with `fixture_id=0` or that asserts it's non-None)

- [ ] **Step 1: Write failing test for nullable `events_snapshot`**

```python
# In tests/test_models.py or a new test — add to existing catalog loader tests
from catalog.loader import CatalogMatch

def test_catalog_match_events_snapshot_can_be_none() -> None:
    m = CatalogMatch(
        match_id="test",
        title="Test",
        home_team="A",
        away_team="B",
        competition="Test",
        season_label="2024",
        events_snapshot=None,
        fixture_id=12345,
    )
    assert m.events_snapshot is None
```

Run: `pytest tests/test_models.py -v -k "events_snapshot" 2>&1 | tail -5`
Expected: error — `events_snapshot: str` rejects `None`

- [ ] **Step 2: Change `CatalogMatch.events_snapshot` to `str | None`**

> **Note:** The `fixture_id` loader line at `catalog/loader.py` line 46 already reads `fixture_id=int(fid) if fid is not None else None` — that nullable handling is already present. Do NOT re-apply it. Only the two `events_snapshot` changes below are needed here.

In `catalog/loader.py` line 23:
```python
# Before
events_snapshot: str

# After
events_snapshot: str | None
```

In `catalog/loader.py` line 45:
```python
# Before
events_snapshot=row["events_snapshot"],

# After
events_snapshot=row.get("events_snapshot") or None,
```

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/test_models.py -v -k "events_snapshot" 2>&1 | tail -5`
Expected: PASS

- [ ] **Step 4: Write failing test for nullable `fixture_id` in `GameState`**

Add to `tests/test_models.py`:
```python
from models.game import GameState

def test_game_state_fixture_id_can_be_none() -> None:
    g = GameState(
        video_id="test",
        home_team="A",
        away_team="B",
        league="PL",
        date="2024-01-01",
        fixture_id=None,
        video_filename="match.mp4",
        source="catalog:test",
        duration_seconds=5400.0,
        kickoff_first_half=300.0,
        kickoff_second_half=3300.0,
    )
    assert g.fixture_id is None

def test_game_state_roundtrip_with_none_fixture_id() -> None:
    g = GameState(
        video_id="test", home_team="A", away_team="B", league="PL",
        date="2024-01-01", fixture_id=None, video_filename="match.mp4",
        source="catalog:test", duration_seconds=5400.0,
        kickoff_first_half=300.0, kickoff_second_half=3300.0,
    )
    restored = GameState.from_dict(g.to_dict())
    assert restored.fixture_id is None
```

Run: `pytest tests/test_models.py -v -k "fixture_id" 2>&1 | tail -5`
Expected: FAIL — `fixture_id: int` rejects `None`

- [ ] **Step 5: Change `GameState.fixture_id` to `int | None`**

In `models/game.py` line 17:
```python
# Before
fixture_id: int

# After
fixture_id: int | None
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_models.py -v -k "fixture_id" 2>&1 | tail -5`
Expected: PASS

- [ ] **Step 7: Run full test suite and fix any breakage from type change**

Run: `pytest -x 2>&1 | tail -20`

Any test constructing `GameState(fixture_id=1, ...)` is fine. Fix only tests that *assert* `fixture_id` is a non-optional `int` or break from the change.

- [ ] **Step 8: Remove API-only fields from `HighlightQuery`**

In `models/highlight_query.py`, remove these three lines:
```python
api_team_id: int | None = None    # DELETE
api_player_id: int | None = None  # DELETE
api_event_type: str | None = None # DELETE
```

- [ ] **Step 9: Run full test suite**

Run: `pytest -x 2>&1 | tail -20`
Expected: failures only where code *uses* `hq.api_team_id`, `hq.api_player_id`, or `hq.api_event_type`. Note which files fail — they are all addressed in later tasks.

> **Expected mypy failures at this point:** `query_interpreter.py` will also fail because it *constructs* `HighlightQuery(api_player_id=..., api_event_type=...)` with kwargs that no longer exist on the dataclass. This is expected and intentional — Task 2 removes that code. Do not fix `query_interpreter.py` here.

- [ ] **Step 10: Commit**

```bash
git add catalog/loader.py models/game.py models/highlight_query.py tests/test_models.py
git commit -m "feat: make events_snapshot and fixture_id nullable; remove API-only query fields"
```

---

## Task 2: Simplify `interpret_query` — remove API calls, change signature

**Files:**
- Modify: `pipeline/query_interpreter.py`
- Modify: `tests/test_query_interpreter.py`

- [ ] **Step 1: Update `test_query_interpreter.py` — change all call sites**

The function signature changes from `(raw_query, game, aligned_events: list[AlignedEvent])` to `(raw_query, game, player_names: list[str])`. Update every call in the test file:

```python
# Every call like:
interpret_query("...", _make_game(), [_make_aligned_event()])
interpret_query("...", _make_game(), [])

# Becomes:
interpret_query("...", _make_game(), ["Mohamed Salah"])
interpret_query("...", _make_game(), [])
```

Also remove the `_make_aligned_event` helper and the `AlignedEvent` import — they are no longer needed here.

Run: `pytest tests/test_query_interpreter.py -v 2>&1 | tail -10`
Expected: FAIL — `interpret_query` still takes old signature

- [ ] **Step 2: Update `interpret_query` in `query_interpreter.py`**

Replace the function and remove dead code:

```python
# REMOVE entire _get_players_map function (lines 73-107)

# REMOVE _EVENTTYPE_TO_API_TYPE dict (lines 61-70)

# Change interpret_query signature:
def interpret_query(
    raw_query: str,
    game: GameState,
    player_names: list[str],          # was: aligned_events: list[AlignedEvent]
) -> HighlightQuery:
    """Interpret *raw_query* using OpenAI and return a structured HighlightQuery."""
    if not OPENAI_API_KEY:
        raise QueryInterpreterError("OPENAI_API_KEY is not set — add it to your .env file")

    # player_names already provided — no API call needed
    user_message = (
        f"Game: {game.home_team} vs {game.away_team} ({game.date})\n"
        f"Available players: {json.dumps(sorted(player_names))}\n\n"
        f"User query: {raw_query}"
    )

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0,
        )
        content = response.choices[0].message.content or ""
        data: dict[str, object] = json.loads(content)

        query_type = QueryType(str(data["query_type"]))
        event_types: list[EventType] | None = None
        raw_event_types = data.get("event_types")
        if raw_event_types:
            event_types = [EventType(et) for et in cast(list[str], raw_event_types)]

        player_name: str | None = data.get("player_name")  # type: ignore[assignment]
        minute_from: int | None = data.get("minute_from")  # type: ignore[assignment]
        minute_to: int | None = data.get("minute_to")      # type: ignore[assignment]

        label = _generate_highlights_label(raw_query, client)

        return HighlightQuery(
            query_type=query_type,
            event_types=event_types,
            player_name=player_name,
            raw_query=raw_query,
            minute_from=minute_from,
            minute_to=minute_to,
            label=label,
        )
    except Exception as exc:
        log.warning("Query interpretation failed (%s) — falling back to FULL_SUMMARY", exc)
        return HighlightQuery(query_type=QueryType.FULL_SUMMARY, raw_query=raw_query)
```

Also remove the `AlignedEvent` import from the top of `query_interpreter.py` if it is now unused.

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_query_interpreter.py -v 2>&1 | tail -10`
Expected: all PASS

- [ ] **Step 4: Run full suite to see remaining failures**

Run: `pytest -x 2>&1 | tail -20`
Expected: failures only in files that call `interpret_query` with the old signature — all addressed in later tasks.

- [ ] **Step 5: Commit**

```bash
git add pipeline/query_interpreter.py tests/test_query_interpreter.py
git commit -m "refactor: interpret_query accepts player_names list instead of aligned_events; remove API Football calls"
```

---

## Task 3: Delete `fetch_filtered_events`

**Files:**
- Modify: `pipeline/match_events.py`
- Modify: `tests/test_match_events.py` (remove any tests for `fetch_filtered_events`)

- [ ] **Step 1: Check if `fetch_filtered_events` is tested**

Run: `grep -n "fetch_filtered_events" tests/test_match_events.py 2>/dev/null || echo "not found"`

If tests exist, remove them now.

- [ ] **Step 2: Delete `fetch_filtered_events` from `match_events.py`**

Remove the entire `fetch_filtered_events` function (lines 95–148). Keep all other functions.

- [ ] **Step 3: Run match_events tests**

Run: `pytest tests/test_match_events.py -v 2>&1 | tail -10`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add pipeline/match_events.py tests/test_match_events.py
git commit -m "refactor: delete fetch_filtered_events — events fetched at ingest time only"
```

---

## Task 4: Simplify `run_catalog_pipeline`

**Files:**
- Modify: `pipeline/catalog_pipeline.py`
- Create: `tests/test_catalog_pipeline.py`

- [ ] **Step 1: Write failing tests for the new query-time flow**

Create `tests/test_catalog_pipeline.py`:

```python
"""Tests for run_catalog_pipeline — no API-Football calls at query time."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from models.events import AlignedEvent, EventType
from models.game import GameState
from pipeline.catalog_pipeline import CatalogPipelineError


def _aligned_event(player: str = "Player A", minute: int = 10) -> dict[str, Any]:
    return {
        "event_type": "goal",
        "minute": minute,
        "extra_minute": None,
        "half": "1st Half",
        "player": player,
        "team": "Home",
        "score": "1-0",
        "detail": "Normal Goal",
        "estimated_video_ts": 900.0,
        "refined_video_ts": 895.0,
        "confidence": 0.9,
        "assist": None,
    }


def _game_dict() -> dict[str, Any]:
    return {
        "video_id": "istanbul-2005",
        "home_team": "Liverpool",
        "away_team": "AC Milan",
        "league": "UEFA Champions League",
        "date": "2004-05",
        "fixture_id": None,
        "video_filename": "match.mp4",
        "source": "catalog:istanbul-2005",
        "duration_seconds": 5400.0,
        "kickoff_first_half": 330.0,
        "kickoff_second_half": 3420.0,
    }


def _aligned_events_dict(events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"video_id": "istanbul-2005", "event_count": 1, "events": events or [_aligned_event()]}


class TestRunCatalogPipelineMissingData:
    def test_missing_aligned_events_raises(self, tmp_path: Path) -> None:
        from utils.storage import LocalStorage

        storage = LocalStorage(tmp_path)
        storage.write_json("istanbul-2005", "game.json", _game_dict())
        # aligned_events.json NOT written

        from pipeline.catalog_pipeline import run_catalog_pipeline

        with pytest.raises(CatalogPipelineError, match="Missing aligned events"):
            run_catalog_pipeline("istanbul-2005", "goals", storage)

    def test_empty_aligned_events_raises(self, tmp_path: Path) -> None:
        from utils.storage import LocalStorage

        storage = LocalStorage(tmp_path)
        storage.write_json("istanbul-2005", "game.json", _game_dict())
        storage.write_json("istanbul-2005", "aligned_events.json", {"events": []})

        from pipeline.catalog_pipeline import run_catalog_pipeline

        with pytest.raises(CatalogPipelineError, match="empty or stale"):
            run_catalog_pipeline("istanbul-2005", "goals", storage)


class TestRunCatalogPipelineNoApiCalls:
    def test_no_fetch_filtered_events_or_align_events_called(self, tmp_path: Path) -> None:
        from utils.storage import LocalStorage

        storage = LocalStorage(tmp_path)
        storage.write_json("istanbul-2005", "game.json", _game_dict())
        storage.write_json(
            "istanbul-2005", "aligned_events.json", _aligned_events_dict()
        )

        mock_hq = MagicMock()
        mock_hq.query_type.value = "full_summary"
        mock_hq.label = "goals"

        with patch("pipeline.catalog_pipeline.interpret_query", return_value=mock_hq) as mock_interp, \
             patch("pipeline.catalog_pipeline.filter_events", return_value=[]), \
             patch("pipeline.catalog_pipeline.build_highlights", return_value={"highlights_path": "/tmp/h.mp4", "clip_count": 0, "total_duration_seconds": 0.0}):
            from pipeline.catalog_pipeline import run_catalog_pipeline

            run_catalog_pipeline("istanbul-2005", "goals", storage)

        # interpret_query must receive player_names as list[str], not AlignedEvent objects
        call_args = mock_interp.call_args
        player_names_arg = call_args[0][2]
        assert isinstance(player_names_arg, list)
        assert all(isinstance(n, str) for n in player_names_arg)

    def test_player_names_extracted_from_aligned_events(self, tmp_path: Path) -> None:
        from utils.storage import LocalStorage

        storage = LocalStorage(tmp_path)
        storage.write_json("istanbul-2005", "game.json", _game_dict())
        events = [
            _aligned_event(player="Mohamed Salah"),
            {**_aligned_event(player="Darwin Nunez", minute=20), "assist": "Mohamed Salah"},
        ]
        storage.write_json(
            "istanbul-2005", "aligned_events.json", {"video_id": "istanbul-2005", "event_count": 2, "events": events}
        )

        captured: list[list[str]] = []

        def capture_interp(raw_query: str, game: Any, player_names: list[str]) -> MagicMock:
            captured.append(player_names)
            m = MagicMock()
            m.query_type.value = "full_summary"
            m.label = "test"
            return m

        with (
            patch("pipeline.catalog_pipeline.interpret_query", side_effect=capture_interp),
            patch("pipeline.catalog_pipeline.filter_events", return_value=[]),
            patch("pipeline.catalog_pipeline.build_highlights", return_value={"highlights_path": "/tmp/h.mp4", "clip_count": 0, "total_duration_seconds": 0.0}),
        ):
            from pipeline.catalog_pipeline import run_catalog_pipeline

            run_catalog_pipeline("istanbul-2005", "goals", storage)

        assert "Mohamed Salah" in captured[0]
        assert "Darwin Nunez" in captured[0]
```

Run: `pytest tests/test_catalog_pipeline.py -v 2>&1 | tail -20`
Expected: some FAIL (pipeline still calls old functions)

- [ ] **Step 2: Rewrite `run_catalog_pipeline`**

Replace the body of `run_catalog_pipeline` in `pipeline/catalog_pipeline.py`:

```python
def run_catalog_pipeline(
    match_id: str,
    highlights_query: str,
    storage: StorageBackend,
    progress_callback: Any = None,
) -> dict[str, Any]:
    """Execute query-time pipeline: load pre-aligned events → LLM → filter → clips."""

    from models.events import AlignedEvent
    from models.game import GameState
    from pipeline.clip_builder import build_highlights
    from pipeline.event_filter import filter_events
    from pipeline.query_interpreter import interpret_query

    # Load pre-processed data (written during ingest)
    try:
        game = GameState.from_dict(storage.read_json(match_id, "game.json"))
    except Exception as exc:
        raise CatalogPipelineError(
            f"Missing ingestion data for {match_id}. Run ingest.py first."
        ) from exc

    if progress_callback:
        progress_callback("loading_events")

    try:
        aligned_data = storage.read_json(match_id, "aligned_events.json")
    except Exception as exc:
        raise CatalogPipelineError(
            f"Missing aligned events for {match_id}. Run ingest.py first."
        ) from exc

    aligned_events = [AlignedEvent.from_dict(e) for e in aligned_data.get("events", [])]
    if not aligned_events:
        raise CatalogPipelineError(
            f"aligned_events.json for {match_id} is empty or stale. Re-run ingest.py."
        )

    # Extract player names from pre-aligned events — no API call
    player_names = sorted({
        name
        for e in aligned_events
        for name in [e.player, e.assist]
        if name
    })

    if progress_callback:
        progress_callback("interpreting_query")

    # LLM interpretation only
    from models.highlight_query import HighlightQuery, QueryType
    try:
        hq = interpret_query(highlights_query, game, player_names)
    except Exception as exc:
        log.warning("Interpreter failed: %s", exc)
        hq = HighlightQuery(query_type=QueryType.FULL_SUMMARY, raw_query=highlights_query)

    if progress_callback:
        progress_callback("filtering")

    # Local filtering — no API call
    filtered = filter_events(aligned_events, hq)

    if progress_callback:
        progress_callback("building_clips")

    # Build final clips
    result = build_highlights(
        filtered,
        game,
        hq,
        storage,
        confirm_overwrite_fn=lambda _path: False,
    )
    result["video_id"] = match_id
    return result
```

Remove `kickoff_first_override`, `kickoff_second_override` parameters from the signature entirely.

- [ ] **Step 3: Run new tests**

Run: `pytest tests/test_catalog_pipeline.py -v 2>&1 | tail -20`
Expected: all PASS

- [ ] **Step 4: Run full test suite**

Run: `pytest -x 2>&1 | tail -20`
Expected: failures only in `test_worker.py` (kickoff params) — addressed in Task 5.

- [ ] **Step 5: Commit**

```bash
git add pipeline/catalog_pipeline.py tests/test_catalog_pipeline.py
git commit -m "refactor: simplify run_catalog_pipeline — load pre-aligned events, no API calls at query time"
```

---

## Task 5: Remove kickoff overrides from worker and API

**Files:**
- Modify: `worker/runner.py`
- Modify: `api/schemas.py`
- Modify: `api/routes/jobs.py`
- Modify: `tests/test_worker.py`
- Modify: `tests/test_api_jobs.py` (if needed)

- [ ] **Step 1: Update `worker/runner.py` — remove all three layers**

**Layer 1 — `_run_pipeline`:** remove `kickoff_first_override` and `kickoff_second_override` params and the corresponding pass-through to `run_catalog_pipeline`.

**Layer 2 — `process_job`:** remove `kickoff_first_override` and `kickoff_second_override` params and the call to `_run_pipeline`.

**Layer 3 — `run_worker` queue extraction:** remove lines 176–177:
```python
# DELETE:
kickoff_first_override=msg.body.get("kickoff_first_half"),
kickoff_second_override=msg.body.get("kickoff_second_half"),
```

Result — `process_job` call in `run_worker` becomes:
```python
process_job(
    job_id=job_id,
    match_id=match_id,
    highlights_query=highlights_query,
    webhook_url=msg.body.get("webhook_url"),
    store=store,
    storage=storage,
)
```

- [ ] **Step 2: Update `test_worker.py`**

Remove `kickoff_first_override` and `kickoff_second_override` keyword args from all `process_job` call sites in the test file.

- [ ] **Step 3: Run worker tests**

Run: `pytest tests/test_worker.py -v 2>&1 | tail -10`
Expected: all PASS

- [ ] **Step 4: Remove kickoff fields from `api/schemas.py`**

Delete lines 20–21:
```python
kickoff_first_half: float | None = Field(None, ge=0)   # DELETE
kickoff_second_half: float | None = Field(None, ge=0)  # DELETE
```

- [ ] **Step 5: Remove kickoff fields from `api/routes/jobs.py`**

In `create_job`, the `queue.send(...)` call currently includes kickoff fields. Remove them:

```python
queue.send(
    {
        "job_id": job.job_id,
        "match_id": job.match_id,
        "highlights_query": job.highlights_query,
        "webhook_url": job.webhook_url,
        # kickoff_first_half and kickoff_second_half REMOVED
    }
)
```

Also remove the cache-bypass condition that checked for kickoff overrides (lines 40–41):
```python
# DELETE this guard — cache should always apply:
if request.kickoff_first_half is None and request.kickoff_second_half is None:
```

The cache lookup block (indented under that guard) should now always run. Dedent it.

- [ ] **Step 6: Run API tests**

Run: `pytest tests/test_api_jobs.py -v 2>&1 | tail -10`
Expected: all PASS. If any test sends `kickoff_first_half`/`kickoff_second_half` in the request body, remove those fields from the test payload.

- [ ] **Step 7: Run full test suite**

Run: `pytest -x 2>&1 | tail -20`
Expected: all PASS (or only failures related to `local_run.py` and `ingest.py` changes from remaining tasks)

- [ ] **Step 8: Commit**

```bash
git add worker/runner.py api/schemas.py api/routes/jobs.py tests/test_worker.py tests/test_api_jobs.py
git commit -m "refactor: remove kickoff override params from worker and API — kickoffs live in game.json"
```

---

## Task 6: Add ingest steps 5–7 (game.json relocation + events + alignment)

**Files:**
- Modify: `ingest.py`
- Modify: `tests/test_ingest.py`

This task does NOT include fixture selection (`_pick_fixture_interactive`) — that is Task 7. Here we just add the mechanics of steps 5–7 and move game.json write.

- [ ] **Step 1: Write failing tests for new ingest steps**

Add to `tests/test_ingest.py`:

```python
class TestIngestFetchesAndAlignsEvents:
    def test_fetch_match_events_called_after_kickoffs(
        self, tmp_storage: LocalStorage
    ) -> None:
        from unittest.mock import MagicMock, patch
        from ingest import _run_catalog_ingest

        fake_metadata = {
            "video_id": "istanbul-2005",
            "home_team": "Liverpool",
            "away_team": "AC Milan",
            "competition": "UEFA Champions League",
            "season_label": "2004-05",
            "fixture_id": None,
            "video_filename": "match.mp4",
            "source": "catalog:istanbul-2005",
            "duration_seconds": 5400.0,
            "events_snapshot": "istanbul-2005",
        }
        fake_transcription = {
            "kickoff_first_half": 330.0,
            "kickoff_second_half": 3420.0,
        }
        fake_events = {"video_id": "istanbul-2005", "event_count": 2, "events": []}
        fake_aligned = {"video_id": "istanbul-2005", "event_count": 0, "events": []}

        with (
            patch("ingest.ingest_local_catalog_match", return_value=fake_metadata),
            patch("ingest.transcribe", return_value=fake_transcription),
            patch("ingest.fetch_match_events", return_value=fake_events) as mock_fetch,
            patch("ingest.align_events", return_value=fake_aligned) as mock_align,
            patch("builtins.input", lambda _: "/tmp/fake.mp4"),
        ):
            _run_catalog_ingest(
                "istanbul-2005",
                tmp_storage,
                confirm_kickoffs_fn=lambda a, b: (330.0, 3420.0),
            )

        mock_fetch.assert_called_once()
        mock_align.assert_called_once()
        # align_events must receive force_recompute=False and save_to_disk=True
        _, align_kwargs = mock_align.call_args
        assert align_kwargs.get("force_recompute") is False
        assert align_kwargs.get("save_to_disk") is True

    def test_fixture_id_written_as_none_for_snapshot_match(
        self, tmp_storage: LocalStorage
    ) -> None:
        from unittest.mock import patch
        from ingest import _run_catalog_ingest

        fake_metadata = {
            "video_id": "istanbul-2005",
            "home_team": "Liverpool",
            "away_team": "AC Milan",
            "competition": "UEFA Champions League",
            "season_label": "2004-05",
            "fixture_id": None,
            "video_filename": "match.mp4",
            "source": "catalog:istanbul-2005",
            "duration_seconds": 5400.0,
            "events_snapshot": "istanbul-2005",
        }
        fake_transcription = {"kickoff_first_half": 330.0, "kickoff_second_half": 3420.0}
        fake_events = {"video_id": "istanbul-2005", "event_count": 0, "events": []}
        fake_aligned = {"video_id": "istanbul-2005", "event_count": 0, "events": []}

        with (
            patch("ingest.ingest_local_catalog_match", return_value=fake_metadata),
            patch("ingest.transcribe", return_value=fake_transcription),
            patch("ingest.fetch_match_events", return_value=fake_events),
            patch("ingest.align_events", return_value=fake_aligned),
            patch("builtins.input", lambda _: "/tmp/fake.mp4"),
        ):
            _run_catalog_ingest(
                "istanbul-2005",
                tmp_storage,
                confirm_kickoffs_fn=lambda a, b: (330.0, 3420.0),
            )

        game_data = tmp_storage.read_json("istanbul-2005", "game.json")
        assert game_data["fixture_id"] is None  # must be None, not 0
```

Run: `pytest tests/test_ingest.py -v -k "FetchesAndAligns" 2>&1 | tail -10`
Expected: FAIL

- [ ] **Step 2: Update `_run_catalog_ingest` in `ingest.py`**

Add imports at top of `ingest.py`:
```python
from pipeline.match_events import fetch_match_events
from pipeline.event_aligner import align_events
```

Restructure `_run_catalog_ingest` to:
1. Keep existing steps 1 (upload) and 2 (transcribe) as-is, but rename prints to `[1/7]` and `[2/7]`.
2. Keep step 3 (kickoff confirm) as `[3/7]`, but **remove the `game.json` write** from end of this block.
3. Add step 4 placeholder comment `# [4/7] Fixture selection — see _pick_fixture_interactive (added in Task 7)`. For now, `fixture_id` comes from `metadata.get("fixture_id")`.
4. Add step 5 — write `game.json`:

```python
# [5/7] Write game.json
print("\n[5/7] Writing game.json…")
from models.game import GameState

fid_raw = metadata.get("fixture_id")
game = GameState(
    video_id=match_id,
    home_team=metadata["home_team"],
    away_team=metadata["away_team"],
    league=metadata["competition"],
    date=metadata["season_label"],
    fixture_id=int(fid_raw) if fid_raw is not None else None,
    video_filename=metadata.get("video_filename", "match.mp4"),
    source=str(metadata.get("source", f"catalog:{match_id}")),
    duration_seconds=float(metadata["duration_seconds"]),
    kickoff_first_half=float(k_first),
    kickoff_second_half=float(k_second),
)
storage.write_json(match_id, "game.json", game.to_dict())
```

5. Add step 6 — fetch events:

```python
# [6/7] Fetch match events
print("\n[6/7] Fetching match events…")
events_data = fetch_match_events(metadata, storage)
print(f"      {events_data.get('event_count', 0)} events loaded.")
```

6. Add step 7 — align events:

```python
# [7/7] Align events to video timestamps
print("\n[7/7] Aligning events to video…")
align_events(
    events_data,
    metadata,
    storage,
    k_first,
    k_second,
    force_recompute=False,
    save_to_disk=True,
)
print("      aligned_events.json written.")
```

7. Update the final print to reflect the new artifacts:
```python
print(
    "\n  Done — game.json, match_events.json, and aligned_events.json written. "
    "You can now use the User CLI to query this game.\n"
)
```

- [ ] **Step 3: Update existing `TestIngestWritesGameJson` tests**

The test `test_game_json_written_after_successful_ingest` currently uses `fixture_id: 0` in `fake_metadata`. Update it:
```python
"fixture_id": None,   # was: 0
```
And add mocks for the new steps:
```python
patch("ingest.fetch_match_events", return_value={"event_count": 0, "events": []}),
patch("ingest.align_events", return_value={"event_count": 0, "events": []}),
```

The assertion `game_data["fixture_id"] is None` (instead of `== 0`) should pass.

- [ ] **Step 4: Run ingest tests**

Run: `pytest tests/test_ingest.py -v 2>&1 | tail -20`
Expected: all PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest -x 2>&1 | tail -20`
Expected: all PASS (except any `local_run.py` runtime issues — addressed in Task 8)

- [ ] **Step 6: Commit**

```bash
git add ingest.py tests/test_ingest.py
git commit -m "feat: add ingest steps 5-7 — write game.json then fetch and align events"
```

---

## Task 7: Add `_pick_fixture_interactive` (ingest step 4)

**Files:**
- Modify: `ingest.py`
- Modify: `tests/test_ingest.py`

- [ ] **Step 1: Write failing tests for fixture selection**

Add to `tests/test_ingest.py`:

```python
class TestPickFixtureInteractive:
    _TEAMS_RESP_HOME = {"response": [{"team": {"id": 40, "name": "Liverpool"}}]}
    _TEAMS_RESP_AWAY = {"response": [{"team": {"id": 50, "name": "AC Milan"}}]}
    _H2H_MULTI = {
        "response": [
            {
                "fixture": {"id": 1001, "date": "2005-05-25"},
                "teams": {
                    "home": {"name": "Liverpool"},
                    "away": {"name": "AC Milan"},
                },
                "league": {"name": "UEFA Champions League"},
            },
            {
                "fixture": {"id": 1002, "date": "2007-05-23"},
                "teams": {
                    "home": {"name": "AC Milan"},
                    "away": {"name": "Liverpool"},
                },
                "league": {"name": "UEFA Champions League"},
            },
        ]
    }
    _H2H_SINGLE = {
        "response": [
            {
                "fixture": {"id": 1001, "date": "2005-05-25"},
                "teams": {
                    "home": {"name": "Liverpool"},
                    "away": {"name": "AC Milan"},
                },
                "league": {"name": "UEFA Champions League"},
            }
        ]
    }
    _H2H_EMPTY = {"response": []}

    def _mock_api(self, responses: list[dict]) -> Any:
        """Returns a context manager that patches urllib.request.urlopen."""
        import io
        import json
        from unittest.mock import MagicMock, patch

        call_count = [0]

        def fake_urlopen(req: Any) -> Any:
            idx = call_count[0]
            call_count[0] += 1
            body = json.dumps(responses[idx % len(responses)]).encode()
            mock_resp = MagicMock()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read.return_value = body
            return mock_resp

        return patch("ingest.urllib.request.urlopen", side_effect=fake_urlopen)

    def test_zero_results_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ingest import _pick_fixture_interactive

        with (
            patch("ingest.API_FOOTBALL_KEY", "test-key"),
            self._mock_api([self._TEAMS_RESP_HOME, self._TEAMS_RESP_AWAY, self._H2H_EMPTY]),
        ):
            result = _pick_fixture_interactive("Liverpool", "AC Milan", "2004-05")
        assert result is None

    def test_single_result_confirmed_returns_fixture_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ingest import _pick_fixture_interactive

        monkeypatch.setattr("builtins.input", lambda _: "y")
        with (
            patch("ingest.API_FOOTBALL_KEY", "test-key"),
            self._mock_api([self._TEAMS_RESP_HOME, self._TEAMS_RESP_AWAY, self._H2H_SINGLE]),
        ):
            result = _pick_fixture_interactive("Liverpool", "AC Milan", "2004-05")
        assert result == 1001

    def test_single_result_rejected_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ingest import _pick_fixture_interactive

        monkeypatch.setattr("builtins.input", lambda _: "n")
        with (
            patch("ingest.API_FOOTBALL_KEY", "test-key"),
            self._mock_api([self._TEAMS_RESP_HOME, self._TEAMS_RESP_AWAY, self._H2H_SINGLE]),
        ):
            result = _pick_fixture_interactive("Liverpool", "AC Milan", "2004-05")
        assert result is None

    def test_multiple_results_operator_picks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ingest import _pick_fixture_interactive

        monkeypatch.setattr("builtins.input", lambda _: "1")
        with (
            patch("ingest.API_FOOTBALL_KEY", "test-key"),
            self._mock_api([self._TEAMS_RESP_HOME, self._TEAMS_RESP_AWAY, self._H2H_MULTI]),
        ):
            result = _pick_fixture_interactive("Liverpool", "AC Milan", "2004-05")
        assert result == 1001

    def test_multiple_results_operator_quits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ingest import _pick_fixture_interactive

        monkeypatch.setattr("builtins.input", lambda _: "q")
        with (
            patch("ingest.API_FOOTBALL_KEY", "test-key"),
            self._mock_api([self._TEAMS_RESP_HOME, self._TEAMS_RESP_AWAY, self._H2H_MULTI]),
        ):
            result = _pick_fixture_interactive("Liverpool", "AC Milan", "2004-05")
        assert result is None
```

Run: `pytest tests/test_ingest.py -v -k "PickFixture" 2>&1 | tail -10`
Expected: FAIL — `_pick_fixture_interactive` not yet defined

- [ ] **Step 2: Implement `_pick_fixture_interactive` in `ingest.py`**

Add these imports at the top of `ingest.py`:
```python
import difflib
import json
import re
import urllib.parse
import urllib.request

from config.settings import API_FOOTBALL_BASE_URL, API_FOOTBALL_KEY
```

Add the function before `_run_catalog_ingest`:

```python
def _pick_fixture_interactive(
    home_team: str,
    away_team: str,
    season_label: str,
) -> int | None:
    """Search API-Football for fixtures matching these teams/season. Operator picks.

    Returns the selected fixture_id, or None if aborted or not found.
    """
    if not API_FOOTBALL_KEY:
        print("  API_FOOTBALL_KEY not set — cannot search for fixture.")
        return None

    def _search_team(name: str) -> int | None:
        url = f"{API_FOOTBALL_BASE_URL}/teams?search={urllib.parse.quote(name)}"
        req = urllib.request.Request(
            url,
            headers={
                "x-rapidapi-key": API_FOOTBALL_KEY,
                "x-rapidapi-host": "v3.football.api-sports.io",
            },
        )
        try:
            with urllib.request.urlopen(req) as resp:  # nosec B310
                body = json.loads(resp.read().decode())
        except Exception as exc:
            print(f"  Team search failed for {name!r}: {exc}")
            return None
        results = body.get("response", [])
        if not results:
            print(f"  No team found for {name!r}.")
            return None
        # pick best match by name similarity
        candidates = [(r["team"]["id"], r["team"]["name"]) for r in results]
        name_lower = name.lower()
        for tid, tname in candidates:
            if name_lower in tname.lower() or tname.lower() in name_lower:
                return tid
        close = difflib.get_close_matches(
            name, [t for _, t in candidates], n=1, cutoff=0.6
        )
        if close:
            return next(tid for tid, t in candidates if t == close[0])
        return candidates[0][0]  # fallback: first result

    home_id = _search_team(home_team)
    away_id = _search_team(away_team)
    if home_id is None or away_id is None:
        return None

    # parse first 4-digit token from season_label
    year_match = re.search(r"\d{4}", season_label)
    season_year = year_match.group() if year_match else ""

    url = f"{API_FOOTBALL_BASE_URL}/fixtures/headtohead?h2h={home_id}-{away_id}"
    if season_year:
        url += f"&season={season_year}"
    req = urllib.request.Request(
        url,
        headers={
            "x-rapidapi-key": API_FOOTBALL_KEY,
            "x-rapidapi-host": "v3.football.api-sports.io",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:  # nosec B310
            body = json.loads(resp.read().decode())
    except Exception as exc:
        print(f"  Fixture search failed: {exc}")
        return None

    fixtures = body.get("response", [])
    if not fixtures:
        print("  No fixtures found for these teams/season.")
        return None

    if len(fixtures) == 1:
        f = fixtures[0]
        fid = f["fixture"]["id"]
        date = f["fixture"].get("date", "")[:10]
        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]
        league = f["league"]["name"]
        print(f"\n  Found: [{fid}] {date}  {home} vs {away}  ({league})")
        ans = input("  Use this fixture? [Y/n] ").strip().lower()
        if ans in ("", "y", "yes"):
            return int(fid)
        return None

    # Multiple results — show picker
    print("\n  Multiple fixtures found:\n")
    for i, f in enumerate(fixtures, 1):
        fid = f["fixture"]["id"]
        date = f["fixture"].get("date", "")[:10]
        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]
        league = f["league"]["name"]
        print(f"  [{i}] {date}  {home} vs {away}  ({league})  (fixture {fid})")
    raw = input("\n  Pick a number (or 'q' to abort): ").strip()
    if raw.lower() == "q":
        return None
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(fixtures):
            return int(fixtures[idx]["fixture"]["id"])
    except ValueError:
        pass
    print("  Invalid choice.")
    return None
```

`urllib.parse` is already included in the imports block above — no separate action needed.

- [ ] **Step 3: Wire `_pick_fixture_interactive` into `_run_catalog_ingest` as step 4**

In `_run_catalog_ingest`, after the kickoff confirmation (step 3) and before writing `game.json` (step 5), add:

```python
# [4/7] Fixture selection (only for matches without a bundled snapshot)
fixture_id_override: int | None = None
if not entry_events_snapshot:  # passed in from catalog entry
    print("\n[4/7] Searching API-Football for fixture…")
    fixture_id_override = _pick_fixture_interactive(
        metadata["home_team"],
        metadata["away_team"],
        metadata.get("season_label", ""),
    )
    if fixture_id_override is None:
        print("  No fixture selected — game.json will have fixture_id=None.")
    else:
        print(f"  Fixture {fixture_id_override} selected.")
        metadata["fixture_id"] = fixture_id_override
else:
    print("\n[4/7] Snapshot match — skipping fixture selection.")
```

The `entry_events_snapshot` value must be available in `_run_catalog_ingest`. This requires either:
- Passing the catalog entry into the function, OR
- Loading it from the catalog inside the function.

The cleanest approach: load the catalog entry by `match_id` at the top of `_run_catalog_ingest`:

```python
from catalog.loader import get_match as _get_catalog_match
entry = _get_catalog_match(match_id)
entry_events_snapshot = entry.events_snapshot if entry else None
```

- [ ] **Step 4: Update `test_game_json_written_after_successful_ingest` to skip fixture selection**

Now that `_pick_fixture_interactive` runs when `events_snapshot` is falsy, the existing `TestIngestWritesGameJson.test_game_json_written_after_successful_ingest` test needs `events_snapshot` set to a truthy value in `fake_metadata` so step 4 is skipped, and a mock for `_pick_fixture_interactive` as a safety net:

```python
# In fake_metadata dict, add:
"events_snapshot": "istanbul-2005",  # truthy → skips fixture selection
```

Also add to the `with (...)` block:
```python
patch("ingest._pick_fixture_interactive") as mock_pick,
```

And after the call, assert:
```python
mock_pick.assert_not_called()
```

- [ ] **Step 5: Add test confirming snapshot match skips fixture selection**

Add to `tests/test_ingest.py` in `TestIngestFetchesAndAlignsEvents`:

```python
def test_pick_fixture_not_called_for_snapshot_match(
    self, tmp_storage: LocalStorage
) -> None:
    from unittest.mock import patch
    from ingest import _run_catalog_ingest

    fake_metadata = {
        "video_id": "istanbul-2005",
        "home_team": "Liverpool",
        "away_team": "AC Milan",
        "competition": "UEFA Champions League",
        "season_label": "2004-05",
        "fixture_id": None,
        "video_filename": "match.mp4",
        "source": "catalog:istanbul-2005",
        "duration_seconds": 5400.0,
        "events_snapshot": "istanbul-2005",
    }
    fake_transcription = {"kickoff_first_half": 330.0, "kickoff_second_half": 3420.0}

    with (
        patch("ingest.ingest_local_catalog_match", return_value=fake_metadata),
        patch("ingest.transcribe", return_value=fake_transcription),
        patch("ingest.fetch_match_events", return_value={"event_count": 0, "events": []}),
        patch("ingest.align_events", return_value={"event_count": 0, "events": []}),
        patch("ingest._pick_fixture_interactive") as mock_pick,
        patch("builtins.input", lambda _: "/tmp/fake.mp4"),
    ):
        _run_catalog_ingest(
            "istanbul-2005",
            tmp_storage,
            confirm_kickoffs_fn=lambda a, b: (330.0, 3420.0),
        )

    mock_pick.assert_not_called()
```

- [ ] **Step 6: Run all ingest tests**

Run: `pytest tests/test_ingest.py -v 2>&1 | tail -20`
Expected: all PASS

- [ ] **Step 7: Run full test suite**

Run: `pytest -x 2>&1 | tail -20`
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add ingest.py tests/test_ingest.py
git commit -m "feat: add _pick_fixture_interactive — interactive API-Football fixture selection during ingest"
```

---

## Task 8: Update `local_run.py`

**Files:**
- Modify: `local_run.py`

No new tests needed (local_run.py is a dev tool with no existing unit tests). Manual verification is sufficient.

- [ ] **Step 1: Remove `--use-cached-events` flag**

In `main()`, delete the argparse block for `--use-cached-events` (lines 244–249). Remove `args.use_cached_events` from the call to `_run_pipeline_local`.

- [ ] **Step 2: Remove `use_cached_events` parameter from `_run_pipeline_local`**

Remove the `use_cached_events: bool = False` parameter from the function signature.

- [ ] **Step 3: Replace stages 2 and 3 with aligned_events load**

Remove imports and calls to `fetch_filtered_events` and `align_events`. Replace the two stages (currently `[2/5]` and `[3/5]`) with a single load step:

```python
# ── Stage 2: Load pre-aligned events ────────────────────────────────────
t0 = time.monotonic()
print("  [2/4] Loading pre-aligned events…")
try:
    aligned_data = storage.read_json(match_id, "aligned_events.json")
except Exception:
    print(
        f"\n  Error: No aligned_events.json for '{match_id}'.\n"
        "  Run `python ingest.py` first.\n",
        file=sys.stderr,
    )
    raise
aligned_events = [AlignedEvent.from_dict(e) for e in aligned_data.get("events", [])]
elapsed = time.monotonic() - t0
print(f"         → {len(aligned_events)} aligned events ({elapsed:.1f}s)\n")
```

- [ ] **Step 4: Update `interpret_query` call**

Replace:
```python
hq = interpret_query(query, game, [])
```
With:
```python
player_names = sorted({
    name
    for e in aligned_events
    for name in [e.player, e.assist]
    if name
})
hq = interpret_query(query, game, player_names)
```

- [ ] **Step 5: Remove `api_event_type` print line**

Delete line 127:
```python
print(f"           api_event_type={hq.api_event_type}  player={hq.player_name}")
```
Replace with:
```python
print(f"           player={hq.player_name}")
```

- [ ] **Step 6: Update stage count strings**

The flow now has 4 stages: `[1/4]` interpret, `[2/4]` load events, `[3/4]` filter, `[4/4]` build clips. Update all `[N/5]` strings to `[N/4]` accordingly.

- [ ] **Step 7: Remove now-unused imports**

Remove from `_run_pipeline_local`:
```python
from pipeline.event_aligner import align_events     # DELETE
from pipeline.match_events import fetch_filtered_events  # DELETE
```

- [ ] **Step 8: Run mypy**

Run: `mypy local_run.py 2>&1 | tail -10`
Expected: no errors

- [ ] **Step 9: Run full test suite**

Run: `pytest 2>&1 | tail -10`
Expected: all PASS

- [ ] **Step 10: Commit**

```bash
git add local_run.py
git commit -m "refactor: update local_run.py — load aligned_events.json, remove use-cached-events flag"
```

---

## Final Verification

- [ ] **Run full test suite with coverage**

```bash
pytest --tb=short 2>&1 | tail -30
```

Expected: all PASS, no references to `fetch_filtered_events`, `_get_players_map`, `api_event_type`, `api_player_id`, `api_team_id`, `kickoff_first_override`, `kickoff_second_override`.

- [ ] **Verify no stray references to deleted symbols**

```bash
grep -r "fetch_filtered_events\|_get_players_map\|api_event_type\|api_player_id\|api_team_id\|kickoff_first_override\|kickoff_second_override" \
  --include="*.py" . | grep -v "test_" | grep -v ".pyc"
```

Expected: no output (all references gone from non-test production code).

- [ ] **Run mypy and ruff**

```bash
mypy . 2>&1 | tail -10
ruff check . 2>&1 | tail -10
```

Expected: no errors.
