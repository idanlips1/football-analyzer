# Pipeline Refactor: Preprocessing + Natural Language Query — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the monolithic end-to-end pipeline into a one-time `ingest.py` preprocessing script and an on-demand `main.py` query REPL that lets users generate highlights from natural language requests.

**Architecture:** A `StorageBackend` protocol abstracts all file I/O so pipeline modules never reference `PIPELINE_WORKSPACE` directly. New data models (`GameState`, `HighlightQuery`) serve as typed contracts between stages. An LLM-powered `query_interpreter` converts natural language into a structured `HighlightQuery` that `event_filter` uses to select events before clip building.

**Tech Stack:** Python 3.12, OpenAI (`openai` SDK, already in requirements), `difflib` (stdlib), `dataclasses`, pytest, `unittest.mock`

---

## File Map

| File | Status | Responsibility |
|------|--------|---------------|
| `models/game.py` | Create | `GameState` dataclass |
| `models/highlight_query.py` | Create | `QueryType` enum + `HighlightQuery` dataclass |
| `utils/storage.py` | Create | `StorageBackend` Protocol + `LocalStorage` |
| `utils/game_registry.py` | Create | `GameRegistry` — scans storage for ready games |
| `pipeline/event_filter.py` | Create | Pure `filter_events` function |
| `pipeline/query_interpreter.py` | Create | LLM → `HighlightQuery` |
| `tests/conftest.py` | Modify | Add `tmp_storage` fixture; remove PIPELINE_WORKSPACE patches incrementally |
| `tests/test_storage.py` | Create | LocalStorage tests |
| `tests/test_game_registry.py` | Create | GameRegistry tests |
| `tests/test_event_filter.py` | Create | event_filter tests (pure) |
| `tests/test_query_interpreter.py` | Create | query_interpreter tests (mocked OpenAI) |
| `pipeline/match_finder.py` | Modify | Accept `StorageBackend` instead of `PIPELINE_WORKSPACE` |
| `pipeline/match_events.py` | Modify | Same |
| `pipeline/transcription.py` | Modify | Same |
| `pipeline/event_aligner.py` | Modify | Accept `StorageBackend` + explicit kickoff floats |
| `pipeline/clip_builder.py` | Modify | New signature: `(events, game, query, storage, *, confirm_overwrite_fn)` |
| `tests/test_match_finder.py` | Modify | Pass `LocalStorage(tmp_path)` |
| `tests/test_match_events.py` | Modify | Same |
| `tests/test_transcription.py` | Modify | Same |
| `tests/test_event_aligner.py` | Modify | Same + pass explicit kickoffs |
| `tests/test_clip_builder.py` | Rewrite | New build_highlights signature |
| `ingest.py` | Create | Ingest entrypoint |
| `tests/test_ingest.py` | Create | End-to-end ingest (mocked) |
| `main.py` | Rewrite | Query REPL |
| `tests/test_main.py` | Modify | Query REPL tests |

---

## Task 1: Data Models

**Files:**
- Create: `models/game.py`
- Create: `models/highlight_query.py`
- Modify: `tests/test_models.py` (append new test classes)

- [ ] **Step 1: Write failing tests for GameState**

Append to `tests/test_models.py`:
```python
from models.game import GameState

class TestGameState:
    def test_roundtrip_serialisation(self) -> None:
        gs = GameState(
            video_id="abc123",
            home_team="Liverpool",
            away_team="Man City",
            league="Premier League",
            date="2024-10-26",
            fixture_id=12345,
            video_filename="match.mp4",
            source="https://www.youtube.com/watch?v=abc123",
            duration_seconds=5400.0,
            kickoff_first_half=330.0,
            kickoff_second_half=3420.0,
        )
        assert GameState.from_dict(gs.to_dict()) == gs

    def test_source_field_present(self) -> None:
        gs = GameState(
            video_id="x", home_team="A", away_team="B", league="L",
            date="2024-01-01", fixture_id=1, video_filename="v.mp4",
            source="https://www.youtube.com/watch?v=x",
            duration_seconds=100.0, kickoff_first_half=10.0,
            kickoff_second_half=60.0,
        )
        assert gs.source == "https://www.youtube.com/watch?v=x"
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_models.py::TestGameState -v
```
Expected: `ImportError: cannot import name 'GameState'`

- [ ] **Step 3: Create `models/game.py`**

```python
"""GameState data model for a preprocessed match."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any


@dataclass
class GameState:
    video_id: str
    home_team: str
    away_team: str
    league: str
    date: str                    # "YYYY-MM-DD"
    fixture_id: int
    video_filename: str          # filename only, e.g. "match.mp4"
    source: str                  # canonical "https://www.youtube.com/watch?v=<id>"
    duration_seconds: float
    kickoff_first_half: float    # seconds in video — hand-confirmed during ingest
    kickoff_second_half: float   # seconds in video — hand-confirmed during ingest

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GameState:
        return cls(**{k: v for k, v in data.items() if k in {f.name for f in dataclasses.fields(cls)}})
```

- [ ] **Step 4: Write failing tests for HighlightQuery**

Append to `tests/test_models.py`:
```python
from models.highlight_query import HighlightQuery, QueryType
from models.events import EventType

class TestHighlightQuery:
    def test_full_summary_defaults(self) -> None:
        q = HighlightQuery(query_type=QueryType.FULL_SUMMARY)
        assert q.event_types is None
        assert q.player_name is None
        assert q.raw_query == ""

    def test_event_filter_with_types(self) -> None:
        q = HighlightQuery(
            query_type=QueryType.EVENT_FILTER,
            event_types=[EventType.GOAL, EventType.PENALTY],
            raw_query="show me goals",
        )
        assert EventType.GOAL in q.event_types  # type: ignore[operator]

    def test_player_query(self) -> None:
        q = HighlightQuery(query_type=QueryType.PLAYER, player_name="Salah")
        assert q.player_name == "Salah"
```

- [ ] **Step 5: Create `models/highlight_query.py`**

```python
"""HighlightQuery — structured representation of a user highlights request."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from models.events import EventType


class QueryType(StrEnum):
    FULL_SUMMARY = "full_summary"
    EVENT_FILTER = "event_filter"
    PLAYER = "player"


@dataclass
class HighlightQuery:
    query_type: QueryType
    event_types: list[EventType] | None = None
    player_name: str | None = None
    raw_query: str = ""
```

- [ ] **Step 6: Run all model tests**

```bash
pytest tests/test_models.py -v
```
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add models/game.py models/highlight_query.py tests/test_models.py
git commit -m "feat: add GameState and HighlightQuery data models"
```

---

## Task 2: StorageBackend + LocalStorage

**Files:**
- Create: `utils/storage.py`
- Create: `tests/test_storage.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_storage.py`:
```python
"""Tests for LocalStorage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.storage import LocalStorage


@pytest.fixture()
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(root=tmp_path)


class TestLocalStorage:
    def test_write_and_read_json(self, storage: LocalStorage) -> None:
        storage.write_json("vid1", "data.json", {"key": "value"})
        result = storage.read_json("vid1", "data.json")
        assert result == {"key": "value"}

    def test_write_creates_directory(self, storage: LocalStorage, tmp_path: Path) -> None:
        storage.write_json("vid1", "data.json", {})
        assert (tmp_path / "vid1").is_dir()

    def test_local_path_returns_path(self, storage: LocalStorage, tmp_path: Path) -> None:
        p = storage.local_path("vid1", "file.mp4")
        assert p == tmp_path / "vid1" / "file.mp4"

    def test_workspace_path_creates_dir(self, storage: LocalStorage, tmp_path: Path) -> None:
        ws = storage.workspace_path("vid1")
        assert ws == tmp_path / "vid1"
        assert ws.is_dir()

    def test_list_games_empty_workspace(self, storage: LocalStorage) -> None:
        assert storage.list_games() == []

    def test_list_games_returns_only_complete_games(
        self, storage: LocalStorage, tmp_path: Path
    ) -> None:
        # Complete game: both files present
        (tmp_path / "vid_complete").mkdir()
        (tmp_path / "vid_complete" / "game.json").write_text("{}")
        (tmp_path / "vid_complete" / "aligned_events.json").write_text("{}")

        # Partial game: only aligned_events.json
        (tmp_path / "vid_partial").mkdir()
        (tmp_path / "vid_partial" / "aligned_events.json").write_text("{}")

        # No files
        (tmp_path / "vid_empty").mkdir()

        result = storage.list_games()
        assert result == ["vid_complete"]

    def test_list_games_missing_root(self, tmp_path: Path) -> None:
        storage = LocalStorage(root=tmp_path / "nonexistent")
        assert storage.list_games() == []
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_storage.py -v
```
Expected: `ImportError: cannot import name 'LocalStorage'`

- [ ] **Step 3: Create `utils/storage.py`**

```python
"""Storage backend abstraction — local filesystem implementation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    def read_json(self, video_id: str, filename: str) -> dict[str, Any]: ...
    def write_json(self, video_id: str, filename: str, data: dict[str, Any]) -> None: ...
    def local_path(self, video_id: str, filename: str) -> Path: ...
    def workspace_path(self, video_id: str) -> Path: ...
    def list_games(self) -> list[str]: ...


class LocalStorage:
    """Filesystem-backed StorageBackend. Root defaults to PIPELINE_WORKSPACE."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def read_json(self, video_id: str, filename: str) -> dict[str, Any]:
        return json.loads((self._root / video_id / filename).read_text())  # type: ignore[no-any-return]

    def write_json(self, video_id: str, filename: str, data: dict[str, Any]) -> None:
        ws = self._root / video_id
        ws.mkdir(parents=True, exist_ok=True)
        (ws / filename).write_text(json.dumps(data, indent=2))

    def local_path(self, video_id: str, filename: str) -> Path:
        return self._root / video_id / filename

    def workspace_path(self, video_id: str) -> Path:
        path = self._root / video_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def list_games(self) -> list[str]:
        if not self._root.exists():
            return []
        return [
            d.name
            for d in sorted(self._root.iterdir())
            if d.is_dir()
            and (d / "game.json").exists()
            and (d / "aligned_events.json").exists()
        ]
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_storage.py -v
```
Expected: all pass

- [ ] **Step 5: Add `tmp_storage` fixture to `conftest.py`**

Add after the `fake_ffprobe_duration` fixture in `tests/conftest.py`:
```python
from utils.storage import LocalStorage

@pytest.fixture()
def tmp_storage(tmp_path: Path) -> LocalStorage:
    """LocalStorage backed by a temporary directory for test isolation."""
    root = tmp_path / "pipeline_workspace"
    root.mkdir()
    return LocalStorage(root=root)
```

- [ ] **Step 6: Run full test suite to verify nothing broken**

```bash
pytest -x -q
```
Expected: all existing tests pass

- [ ] **Step 7: Commit**

```bash
git add utils/storage.py tests/test_storage.py tests/conftest.py
git commit -m "feat: add StorageBackend protocol and LocalStorage implementation"
```

---

## Task 3: GameRegistry

**Files:**
- Create: `utils/game_registry.py`
- Create: `tests/test_game_registry.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_game_registry.py`:
```python
"""Tests for GameRegistry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from models.game import GameState
from utils.game_registry import GameRegistry
from utils.storage import LocalStorage


def _write_game(storage: LocalStorage, video_id: str, **kwargs: object) -> GameState:
    """Helper: write a game.json and empty aligned_events.json to storage."""
    defaults = dict(
        video_id=video_id,
        home_team="Liverpool",
        away_team="Man City",
        league="Premier League",
        date="2024-10-26",
        fixture_id=12345,
        video_filename="match.mp4",
        source=f"https://www.youtube.com/watch?v={video_id}",
        duration_seconds=5400.0,
        kickoff_first_half=330.0,
        kickoff_second_half=3420.0,
    )
    defaults.update(kwargs)
    gs = GameState(**defaults)  # type: ignore[arg-type]
    storage.write_json(video_id, "game.json", gs.to_dict())
    storage.write_json(video_id, "aligned_events.json", {"events": [], "event_count": 0})
    return gs


class TestGameRegistry:
    def test_list_ready_empty(self, tmp_storage: LocalStorage) -> None:
        registry = GameRegistry(tmp_storage)
        assert registry.list_ready() == []

    def test_list_ready_returns_game_state(self, tmp_storage: LocalStorage) -> None:
        expected = _write_game(tmp_storage, "vid1")
        registry = GameRegistry(tmp_storage)
        result = registry.list_ready()
        assert len(result) == 1
        assert result[0] == expected

    def test_list_ready_excludes_partial_ingest(self, tmp_storage: LocalStorage) -> None:
        # Write game.json but NOT aligned_events.json
        gs = GameState(
            video_id="partial", home_team="A", away_team="B", league="L",
            date="2024-01-01", fixture_id=1, video_filename="v.mp4",
            source="https://www.youtube.com/watch?v=partial",
            duration_seconds=100.0, kickoff_first_half=10.0, kickoff_second_half=60.0,
        )
        tmp_storage.write_json("partial", "game.json", gs.to_dict())
        # No aligned_events.json written
        registry = GameRegistry(tmp_storage)
        assert registry.list_ready() == []

    def test_list_ready_multiple_games(self, tmp_storage: LocalStorage) -> None:
        _write_game(tmp_storage, "vid1")
        _write_game(tmp_storage, "vid2")
        registry = GameRegistry(tmp_storage)
        assert len(registry.list_ready()) == 2
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_game_registry.py -v
```
Expected: `ImportError: cannot import name 'GameRegistry'`

- [ ] **Step 3: Create `utils/game_registry.py`**

```python
"""GameRegistry — discovers and loads ready games from storage."""

from __future__ import annotations

from models.game import GameState
from utils.storage import StorageBackend


class GameRegistry:
    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    def list_ready(self) -> list[GameState]:
        """Return all games that have both game.json and aligned_events.json."""
        games: list[GameState] = []
        for video_id in self._storage.list_games():
            data = self._storage.read_json(video_id, "game.json")
            games.append(GameState.from_dict(data))
        return games
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_game_registry.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add utils/game_registry.py tests/test_game_registry.py tests/conftest.py
git commit -m "feat: add GameRegistry and tmp_storage fixture"
```

---

## Task 4: event_filter

**Files:**
- Create: `pipeline/event_filter.py`
- Create: `tests/test_event_filter.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_event_filter.py`:
```python
"""Tests for event_filter — pure function, no mocks needed."""

from __future__ import annotations

from models.events import AlignedEvent, EventType
from models.highlight_query import HighlightQuery, QueryType
from pipeline.event_filter import filter_events


def _ae(
    event_type: EventType = EventType.GOAL,
    player: str = "Test Player",
    minute: int = 50,
) -> AlignedEvent:
    return AlignedEvent(
        event_type=event_type,
        minute=minute,
        extra_minute=None,
        half="2nd Half",
        player=player,
        team="Test FC",
        score="1 - 0",
        detail="Normal Goal",
        estimated_video_ts=1000.0,
        refined_video_ts=1000.0,
        confidence=0.9,
    )


EVENTS = [
    _ae(EventType.GOAL, "Mohamed Salah", 21),
    _ae(EventType.YELLOW_CARD, "Ruben Dias", 42),
    _ae(EventType.GOAL, "Julian Alvarez", 70),
    _ae(EventType.PENALTY, "Mohamed Salah", 83),
    _ae(EventType.RED_CARD, "John Doe", 88),
]


class TestFullSummary:
    def test_returns_all_events(self) -> None:
        q = HighlightQuery(query_type=QueryType.FULL_SUMMARY)
        assert filter_events(EVENTS, q) == EVENTS


class TestEventFilter:
    def test_goals_only(self) -> None:
        q = HighlightQuery(query_type=QueryType.EVENT_FILTER, event_types=[EventType.GOAL])
        result = filter_events(EVENTS, q)
        assert len(result) == 2
        assert all(e.event_type == EventType.GOAL for e in result)

    def test_goals_and_penalties(self) -> None:
        q = HighlightQuery(
            query_type=QueryType.EVENT_FILTER,
            event_types=[EventType.GOAL, EventType.PENALTY],
        )
        result = filter_events(EVENTS, q)
        assert len(result) == 3

    def test_none_event_types_falls_back_to_all(self) -> None:
        q = HighlightQuery(query_type=QueryType.EVENT_FILTER, event_types=None)
        result = filter_events(EVENTS, q)
        assert result == EVENTS

    def test_no_matches_falls_back_to_all(self) -> None:
        q = HighlightQuery(query_type=QueryType.EVENT_FILTER, event_types=[EventType.CORNER])
        result = filter_events(EVENTS, q)
        assert result == EVENTS


class TestPlayerFilter:
    def test_exact_name_match(self) -> None:
        q = HighlightQuery(query_type=QueryType.PLAYER, player_name="Mohamed Salah")
        result = filter_events(EVENTS, q)
        assert len(result) == 2
        assert all(e.player == "Mohamed Salah" for e in result)

    def test_fuzzy_name_match(self) -> None:
        q = HighlightQuery(query_type=QueryType.PLAYER, player_name="Salah")
        result = filter_events(EVENTS, q)
        assert len(result) == 2

    def test_substring_fallback(self) -> None:
        q = HighlightQuery(query_type=QueryType.PLAYER, player_name="Alvarez")
        result = filter_events(EVENTS, q)
        assert len(result) == 1
        assert result[0].player == "Julian Alvarez"

    def test_no_player_name_falls_back_to_all(self) -> None:
        q = HighlightQuery(query_type=QueryType.PLAYER, player_name=None)
        result = filter_events(EVENTS, q)
        assert result == EVENTS

    def test_unknown_player_falls_back_to_all(self) -> None:
        q = HighlightQuery(query_type=QueryType.PLAYER, player_name="Nonexistent Player XYZ")
        result = filter_events(EVENTS, q)
        assert result == EVENTS
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_event_filter.py -v
```
Expected: `ImportError: cannot import name 'filter_events'`

- [ ] **Step 3: Create `pipeline/event_filter.py`**

```python
"""Event filter — pure function: filters AlignedEvents by a HighlightQuery."""

from __future__ import annotations

import difflib

from models.events import AlignedEvent
from models.highlight_query import HighlightQuery, QueryType
from utils.logger import get_logger

log = get_logger(__name__)


def filter_events(
    events: list[AlignedEvent],
    query: HighlightQuery,
) -> list[AlignedEvent]:
    """Filter *events* according to *query*.

    Always returns at least one event — falls back to the full list if
    filtering produces an empty result.
    """
    if query.query_type == QueryType.FULL_SUMMARY:
        return events

    if query.query_type == QueryType.EVENT_FILTER:
        if query.event_types is None:
            log.warning("EVENT_FILTER query has no event_types — returning all events")
            return events
        filtered = [e for e in events if e.event_type in query.event_types]

    elif query.query_type == QueryType.PLAYER:
        if query.player_name is None:
            log.warning("PLAYER query has no player_name — returning all events")
            return events
        filtered = _filter_by_player(events, query.player_name)

    else:
        return events

    if not filtered:
        print(
            f"  Warning: no events matched '{query.raw_query}' "
            "— showing full highlights instead."
        )
        return events

    return filtered


def _filter_by_player(events: list[AlignedEvent], player_name: str) -> list[AlignedEvent]:
    all_players = list({e.player for e in events if e.player})
    matches = difflib.get_close_matches(player_name, all_players, n=1, cutoff=0.6)

    if matches:
        matched = matches[0]
    else:
        # Substring fallback
        lower = player_name.lower()
        matched = next((p for p in all_players if lower in p.lower()), None)

    if matched is None:
        return []
    return [e for e in events if e.player == matched]
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_event_filter.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add pipeline/event_filter.py tests/test_event_filter.py
git commit -m "feat: add event_filter pure function"
```

---

## Task 5: query_interpreter

**Files:**
- Create: `pipeline/query_interpreter.py`
- Create: `tests/test_query_interpreter.py`

Note: `openai` is already in `requirements.txt`. Verify with `grep openai requirements.txt`. If missing, add it.

- [ ] **Step 1: Verify openai in requirements**

```bash
grep -i openai "/Volumes/Encrypted Extreme SSD/3rd year/1st semester/Advanced Systems dev/football-analyzer/requirements.txt"
```
If missing: add `openai>=1.0.0` to `requirements.txt` and run `pip install openai`.
If you added it, include `requirements.txt` in the commit at Step 6.

- [ ] **Step 2: Write failing tests**

Create `tests/test_query_interpreter.py`:
```python
"""Tests for query_interpreter — mocks OpenAI HTTP calls."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from models.events import AlignedEvent, EventType
from models.game import GameState
from models.highlight_query import HighlightQuery, QueryType
from pipeline.query_interpreter import QueryInterpreterError, interpret_query


def _make_game() -> GameState:
    return GameState(
        video_id="abc",
        home_team="Liverpool",
        away_team="Man City",
        league="Premier League",
        date="2024-10-26",
        fixture_id=1,
        video_filename="match.mp4",
        source="https://www.youtube.com/watch?v=abc",
        duration_seconds=5400.0,
        kickoff_first_half=330.0,
        kickoff_second_half=3420.0,
    )


def _make_aligned_event(player: str = "Mohamed Salah", event_type: EventType = EventType.GOAL) -> AlignedEvent:
    return AlignedEvent(
        event_type=event_type, minute=21, extra_minute=None, half="1st Half",
        player=player, team="Liverpool", score="1 - 0", detail="Normal Goal",
        estimated_video_ts=1590.0, refined_video_ts=1590.0, confidence=0.9,
    )


def _mock_openai_response(content: str) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


class TestInterpretQuery:
    def test_full_summary_response(self) -> None:
        payload = json.dumps({"query_type": "full_summary", "event_types": None, "player_name": None})
        with patch("pipeline.query_interpreter.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _mock_openai_response(payload)
            result = interpret_query("show me everything", _make_game(), [_make_aligned_event()])
        assert result.query_type == QueryType.FULL_SUMMARY

    def test_event_filter_response(self) -> None:
        payload = json.dumps({"query_type": "event_filter", "event_types": ["goal", "penalty"], "player_name": None})
        with patch("pipeline.query_interpreter.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _mock_openai_response(payload)
            result = interpret_query("just goals and penalties", _make_game(), [_make_aligned_event()])
        assert result.query_type == QueryType.EVENT_FILTER
        assert EventType.GOAL in (result.event_types or [])

    def test_player_response(self) -> None:
        payload = json.dumps({"query_type": "player", "event_types": None, "player_name": "Mohamed Salah"})
        with patch("pipeline.query_interpreter.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _mock_openai_response(payload)
            result = interpret_query("Salah moments", _make_game(), [_make_aligned_event()])
        assert result.query_type == QueryType.PLAYER
        assert result.player_name == "Mohamed Salah"

    def test_malformed_response_falls_back_to_full_summary(self) -> None:
        with patch("pipeline.query_interpreter.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _mock_openai_response("not json at all")
            result = interpret_query("anything", _make_game(), [])
        assert result.query_type == QueryType.FULL_SUMMARY

    def test_api_exception_falls_back_to_full_summary(self) -> None:
        with patch("pipeline.query_interpreter.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.side_effect = Exception("network error")
            result = interpret_query("anything", _make_game(), [])
        assert result.query_type == QueryType.FULL_SUMMARY

    def test_missing_api_key_raises_error(self) -> None:
        with patch("pipeline.query_interpreter.OPENAI_API_KEY", ""):
            with pytest.raises(QueryInterpreterError, match="OPENAI_API_KEY"):
                interpret_query("anything", _make_game(), [])

    def test_raw_query_preserved(self) -> None:
        payload = json.dumps({"query_type": "full_summary", "event_types": None, "player_name": None})
        with patch("pipeline.query_interpreter.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _mock_openai_response(payload)
            result = interpret_query("my raw query", _make_game(), [])
        assert result.raw_query == "my raw query"
```

- [ ] **Step 3: Run to verify failure**

```bash
pytest tests/test_query_interpreter.py -v
```
Expected: `ImportError: cannot import name 'interpret_query'`

- [ ] **Step 4: Create `pipeline/query_interpreter.py`**

```python
"""Query interpreter — converts natural language to HighlightQuery via OpenAI."""

from __future__ import annotations

import json

from openai import OpenAI

from config.settings import OPENAI_API_KEY, OPENAI_MODEL
from models.events import AlignedEvent, EventType
from models.game import GameState
from models.highlight_query import HighlightQuery, QueryType
from utils.logger import get_logger

log = get_logger(__name__)


class QueryInterpreterError(Exception):
    """Raised on hard pre-call failures (e.g. missing API key)."""


_SYSTEM_PROMPT = """\
You are a football highlights assistant. Given a user query, return a JSON object.

JSON schema:
{
  "query_type": "full_summary" | "event_filter" | "player",
  "event_types": [list of event type strings] | null,
  "player_name": "exact player name from the provided list" | null
}

Valid event_type strings: goal, own_goal, penalty, red_card, yellow_card, var_review,
card, near_miss, save, shot_on_target, free_kick, corner, substitution, other

Rules:
- For general/summary queries → use full_summary
- For event-type queries (e.g. "just goals", "cards and VAR") → use event_filter + event_types
- For player queries (e.g. "Salah moments") → use player + player_name (exact name from list)

Return ONLY valid JSON, nothing else.\
"""


def interpret_query(
    raw_query: str,
    game: GameState,
    aligned_events: list[AlignedEvent],
) -> HighlightQuery:
    """Interpret *raw_query* using OpenAI and return a structured HighlightQuery.

    Falls back to FULL_SUMMARY on any LLM or parsing failure.
    Raises QueryInterpreterError only if OPENAI_API_KEY is missing.
    """
    if not OPENAI_API_KEY:
        raise QueryInterpreterError("OPENAI_API_KEY is not set — add it to your .env file")

    players = sorted({e.player for e in aligned_events if e.player})
    event_types_present = sorted({e.event_type.value for e in aligned_events})

    user_message = (
        f"Game: {game.home_team} vs {game.away_team} ({game.date})\n"
        f"Available players: {', '.join(players) or 'none'}\n"
        f"Event types in this match: {', '.join(event_types_present) or 'none'}\n\n"
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
        if data.get("event_types"):
            event_types = [EventType(et) for et in data["event_types"]]  # type: ignore[union-attr]

        return HighlightQuery(
            query_type=query_type,
            event_types=event_types,
            player_name=data.get("player_name"),  # type: ignore[arg-type]
            raw_query=raw_query,
        )
    except Exception as exc:
        log.warning("Query interpretation failed (%s) — falling back to FULL_SUMMARY", exc)
        return HighlightQuery(query_type=QueryType.FULL_SUMMARY, raw_query=raw_query)
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_query_interpreter.py -v
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add pipeline/query_interpreter.py tests/test_query_interpreter.py
git commit -m "feat: add LLM-based query interpreter with OpenAI fallback"
```

---

## Task 6: Migrate match_events to StorageBackend

This is the simplest migration — `match_events.py` only reads and writes one JSON file.

**Files:**
- Modify: `pipeline/match_events.py`
- Modify: `tests/test_match_events.py`
- Modify: `tests/conftest.py` (remove `pipeline.match_events.PIPELINE_WORKSPACE` patch)

- [ ] **Step 1: Add `storage` parameter to `fetch_match_events`**

In `pipeline/match_events.py`:
1. Remove `PIPELINE_WORKSPACE` from the import of `config.settings`
2. Add `from utils.storage import StorageBackend` import
3. Change signature to `fetch_match_events(metadata: dict[str, Any], storage: StorageBackend) -> dict[str, Any]`
4. Replace:
   ```python
   workspace = PIPELINE_WORKSPACE / video_id
   workspace.mkdir(parents=True, exist_ok=True)
   cache_path = workspace / MATCH_EVENTS_FILENAME
   if cache_path.exists():
       cached: dict[str, Any] = json.loads(cache_path.read_text())
       return cached
   ...
   cache_path.write_text(json.dumps(result, indent=2))
   ```
   With:
   ```python
   cache_path = storage.local_path(video_id, MATCH_EVENTS_FILENAME)
   if cache_path.exists():
       return storage.read_json(video_id, MATCH_EVENTS_FILENAME)
   ...
   storage.write_json(video_id, MATCH_EVENTS_FILENAME, result)
   ```

- [ ] **Step 2: Update tests in `tests/test_match_events.py`**

Find all calls to `fetch_match_events(metadata)` and change to `fetch_match_events(metadata, tmp_storage)`. Add `tmp_storage` as a fixture parameter. Example:
```python
def test_fetch_match_events_cache_hit(
    tmp_workspace: Path, tmp_storage: LocalStorage
) -> None:
    # write cache file
    tmp_storage.write_json("vid1", "match_events.json", {...})
    result = fetch_match_events({"video_id": "vid1", "fixture_id": 123}, tmp_storage)
    assert result["video_id"] == "vid1"
```

- [ ] **Step 3: Remove `match_events` patch from `tmp_workspace` in `conftest.py`**

Delete this line from `tmp_workspace`:
```python
monkeypatch.setattr("pipeline.match_events.PIPELINE_WORKSPACE", workspace)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_match_events.py -v
```
Expected: all pass

- [ ] **Step 5: Run full suite to check nothing else broke**

```bash
pytest -x -q
```

- [ ] **Step 6: Commit**

```bash
git add pipeline/match_events.py tests/test_match_events.py tests/conftest.py
git commit -m "refactor: migrate match_events to StorageBackend"
```

---

## Task 7: Migrate match_finder to StorageBackend

**Files:**
- Modify: `pipeline/match_finder.py`
- Modify: `tests/test_match_finder.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Update `match_finder.py`**

1. Remove `PIPELINE_WORKSPACE` from `config.settings` imports
2. Add `from utils.storage import StorageBackend` import
3. Change `download_and_save(url, fixture_id, skip_duration_check, storage: StorageBackend)` — add `storage` param
4. Change `load_existing_metadata(video_id, storage: StorageBackend)` — add `storage` param
5. Inside both functions, replace:
   - `workspace = PIPELINE_WORKSPACE / video_id; workspace.mkdir(...)` → `workspace = storage.workspace_path(video_id)`
   - `(workspace / METADATA_FILENAME).write_text(json.dumps(meta))` → `storage.write_json(video_id, METADATA_FILENAME, meta)`
   - `cache_path = PIPELINE_WORKSPACE / video_id / METADATA_FILENAME; if cache_path.exists(): return json.loads(...)` → `cache_path = storage.local_path(video_id, METADATA_FILENAME); if cache_path.exists(): return storage.read_json(...)`

- [ ] **Step 2: Update `tests/test_match_finder.py`**

Pass `tmp_storage` to all `download_and_save` and `load_existing_metadata` calls.

- [ ] **Step 3: Remove `match_finder` patch from `tmp_workspace` in `conftest.py`**

Delete:
```python
monkeypatch.setattr("pipeline.match_finder.PIPELINE_WORKSPACE", workspace)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_match_finder.py -v && pytest -x -q
```

- [ ] **Step 5: Commit**

```bash
git add pipeline/match_finder.py tests/test_match_finder.py tests/conftest.py
git commit -m "refactor: migrate match_finder to StorageBackend"
```

---

## Task 8: Migrate transcription to StorageBackend

**Files:**
- Modify: `pipeline/transcription.py`
- Modify: `tests/test_transcription.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Update `transcription.py`**

1. Remove `PIPELINE_WORKSPACE` from `config.settings` imports
2. Add `from utils.storage import StorageBackend`
3. Change `transcribe(metadata: dict[str, Any], storage: StorageBackend) -> dict[str, Any]`
4. Replace all `PIPELINE_WORKSPACE / video_id / ...` path constructions with equivalent `storage.*` calls. The audio file path (needed by ffmpeg `extract_audio`) should use `storage.workspace_path(video_id) / AUDIO_FILENAME`. The transcription cache should use `storage.write_json` / `storage.read_json`.

- [ ] **Step 2: Update `tests/test_transcription.py`**

Pass `tmp_storage` to all `transcribe(metadata)` calls → `transcribe(metadata, tmp_storage)`.

- [ ] **Step 3: Remove transcription patch from `tmp_workspace`**

Delete:
```python
# pipeline.transcription does not directly import PIPELINE_WORKSPACE
# (it used to — verify this line exists before deleting)
```
Check first: `grep -n "transcription" tests/conftest.py`. Remove if present.

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_transcription.py -v && pytest -x -q
```

- [ ] **Step 5: Commit**

```bash
git add pipeline/transcription.py tests/test_transcription.py tests/conftest.py
git commit -m "refactor: migrate transcription to StorageBackend"
```

---

## Task 9: Migrate event_aligner to StorageBackend + explicit kickoffs

This migration is slightly different: `event_aligner` now receives confirmed kickoff timestamps explicitly and reads utterances from storage itself.

**Files:**
- Modify: `pipeline/event_aligner.py`
- Modify: `tests/test_event_aligner.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Update `align_events` signature in `event_aligner.py`**

Old:
```python
def align_events(
    match_events_data: dict[str, Any],
    transcription: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
```

New:
```python
def align_events(
    match_events_data: dict[str, Any],
    metadata: dict[str, Any],
    storage: StorageBackend,
    kickoff_first: float,
    kickoff_second: float,
) -> dict[str, Any]:
```

Inside the function:
1. Load utterances from storage: `transcription = storage.read_json(video_id, "transcription.json"); utterances = transcription.get("utterances", [])`
2. Remove the `kickoff_first: float | None = transcription.get(...)` extraction (now passed explicitly)
3. Remove the `if kickoff_first is None or kickoff_second is None: raise EventAlignerError(...)` guard (caller guarantees valid floats)
4. Replace `workspace = PIPELINE_WORKSPACE / video_id` with `cache_path = storage.local_path(video_id, ALIGNMENT_FILENAME)`
5. Use `storage.write_json(video_id, ALIGNMENT_FILENAME, result)` for writing

- [ ] **Step 2: Update `tests/test_event_aligner.py`**

For tests that call `align_events`, update signature. The `transcription` argument is removed; instead pass `storage` + `kickoff_first` + `kickoff_second`. Write the transcription JSON to `tmp_storage` before calling.

```python
def test_align_events_basic(
    tmp_storage: LocalStorage,
    sample_match_events: list[MatchEvent],
    sample_transcription_with_kickoff: dict[str, Any],
) -> None:
    video_id = "test_video"
    tmp_storage.write_json(video_id, "transcription.json", sample_transcription_with_kickoff)
    match_events_data = {
        "video_id": video_id,
        "fixture_id": 1,
        "event_count": len(sample_match_events),
        "events": [e.to_dict() for e in sample_match_events],
    }
    metadata = {"video_id": video_id}
    result = align_events(
        match_events_data, metadata, tmp_storage,
        kickoff_first=330.0, kickoff_second=3420.0,
    )
    assert result["event_count"] > 0
```

- [ ] **Step 3: Remove `event_aligner` patch from `tmp_workspace`**

Delete:
```python
monkeypatch.setattr("pipeline.event_aligner.PIPELINE_WORKSPACE", workspace)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_event_aligner.py -v && pytest -x -q
```

- [ ] **Step 5: Commit**

```bash
git add pipeline/event_aligner.py tests/test_event_aligner.py tests/conftest.py
git commit -m "refactor: migrate event_aligner to StorageBackend with explicit kickoffs"
```

---

## Task 10: Refactor clip_builder

**Files:**
- Modify: `pipeline/clip_builder.py`
- Rewrite: `tests/test_clip_builder.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Rewrite `tests/test_clip_builder.py` for the new signature**

The pure functions (`calculate_clip_windows`, `merge_clips`, `enforce_budget`) are unchanged — keep those tests. Rewrite `TestBuildHighlights` class:

```python
from models.events import AlignedEvent, EventType
from models.game import GameState
from models.highlight_query import HighlightQuery, QueryType
from utils.storage import LocalStorage

def _make_game(tmp_storage: LocalStorage, video_id: str = "test_video") -> GameState:
    gs = GameState(
        video_id=video_id, home_team="A", away_team="B", league="L",
        date="2024-01-01", fixture_id=1, video_filename="video.mp4",
        source=f"https://www.youtube.com/watch?v={video_id}",
        duration_seconds=5400.0, kickoff_first_half=330.0, kickoff_second_half=3420.0,
    )
    return gs

def _make_aligned_events() -> list[AlignedEvent]:
    return [AlignedEvent(
        event_type=EventType.GOAL, minute=21, extra_minute=None,
        half="1st Half", player="Test Player", team="Test FC",
        score="1-0", detail="Normal Goal",
        estimated_video_ts=1590.0, refined_video_ts=1590.0, confidence=0.9,
    )]

class TestBuildHighlights:
    def test_build_creates_highlights(
        self, tmp_storage: LocalStorage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        video_id = "test_video"
        game = _make_game(tmp_storage, video_id)
        ws = tmp_storage.workspace_path(video_id)
        (ws / "video.mp4").write_bytes(b"fake")

        monkeypatch.setattr("pipeline.clip_builder.cut_clip", lambda *a, **kw: None)
        monkeypatch.setattr("pipeline.clip_builder.concat_clips", lambda *a, **kw: (ws / "out.mp4").write_bytes(b""))
        monkeypatch.setattr("pipeline.clip_builder.get_video_duration", lambda _: 120.0)

        q = HighlightQuery(query_type=QueryType.FULL_SUMMARY, raw_query="summary")
        result = build_highlights(
            _make_aligned_events(), game, q, tmp_storage,
            confirm_overwrite_fn=lambda _: True,
        )
        assert "highlights_path" in result
        assert result["clip_count"] == 1

    def test_slug_collision_skip(
        self, tmp_storage: LocalStorage
    ) -> None:
        video_id = "test_video"
        game = _make_game(tmp_storage, video_id)
        ws = tmp_storage.workspace_path(video_id)
        # Pre-create the output file
        (ws / "highlights_summary.mp4").write_bytes(b"existing")

        q = HighlightQuery(query_type=QueryType.FULL_SUMMARY, raw_query="summary")
        result = build_highlights(
            _make_aligned_events(), game, q, tmp_storage,
            confirm_overwrite_fn=lambda _: False,  # User says "don't overwrite"
        )
        # Should return cached result without cutting
        assert "highlights_path" in result
```

- [ ] **Step 2: Run new clip_builder tests to verify they fail**

```bash
pytest tests/test_clip_builder.py::TestBuildHighlights -v
```

- [ ] **Step 3: Rewrite `build_highlights` in `pipeline/clip_builder.py`**

1. Add imports: `from models.events import AlignedEvent; from models.game import GameState; from models.highlight_query import HighlightQuery; from utils.storage import StorageBackend`
2. Remove `PIPELINE_WORKSPACE` import from settings
3. Add type alias and helper:
   ```python
   import re
   from collections.abc import Callable
   ConfirmOverwriteFn = Callable[[str], bool]

   def _interactive_confirm_overwrite(path: str) -> bool:
       choice = input(f"  '{path}' already exists. Overwrite? [Y/n] ").strip().lower()
       return choice in ("", "y", "yes")

   def _query_slug(query: HighlightQuery) -> str:
       base = query.raw_query.lower()
       slug = re.sub(r"[^a-z0-9]+", "_", base).strip("_")[:40]
       return slug or query.query_type.value
   ```
4. New `build_highlights` signature and body (see spec for full logic — key points):
   - Output path: `storage.workspace_path(game.video_id) / f"highlights_{_query_slug(query)}.mp4"`
   - Video path: `storage.workspace_path(game.video_id) / game.video_filename`
   - Clips dir: `storage.workspace_path(game.video_id) / "clips"`
   - Write manifest via: `(storage.workspace_path(game.video_id) / MANIFEST_FILENAME).write_text(...)`
   - Existing file check: call `confirm_overwrite_fn(str(output_path))`

- [ ] **Step 4: Remove `clip_builder` patch from `tmp_workspace` in `conftest.py`**

Delete:
```python
monkeypatch.setattr("pipeline.clip_builder.PIPELINE_WORKSPACE", workspace)
```

- [ ] **Step 5: Run all clip_builder tests**

```bash
pytest tests/test_clip_builder.py -v && pytest -x -q
```

- [ ] **Step 6: Commit**

```bash
git add pipeline/clip_builder.py tests/test_clip_builder.py tests/conftest.py
git commit -m "refactor: clip_builder — new signature with StorageBackend and HighlightQuery"
```

---

## Task 11: ingest.py

**Files:**
- Create: `ingest.py`
- Create: `tests/test_ingest.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_ingest.py`:
```python
"""Tests for ingest.py — all external I/O mocked."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from models.game import GameState
from utils.storage import LocalStorage


class TestConfirmKickoffsInteractive:
    """Test the interactive kickoff confirmation helper."""

    def test_auto_detected_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ingest import _confirm_kickoffs_interactive
        monkeypatch.setattr("builtins.input", lambda _: "y")
        first, second = _confirm_kickoffs_interactive(330.0, 3420.0)
        assert first == 330.0
        assert second == 3420.0

    def test_auto_detected_rejected_manual_entry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ingest import _confirm_kickoffs_interactive
        responses = iter(["n", "5:30", "y", "57:00"])
        monkeypatch.setattr("builtins.input", lambda _: next(responses))
        first, second = _confirm_kickoffs_interactive(330.0, 3420.0)
        assert first == 330.0  # 5:30 = 330s
        assert second == 3420.0  # 57:00 = 3420s

    def test_none_detected_requires_manual_entry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ingest import _confirm_kickoffs_interactive
        responses = iter(["2:00", "48:00"])
        monkeypatch.setattr("builtins.input", lambda _: next(responses))
        first, second = _confirm_kickoffs_interactive(None, None)
        assert first == 120.0
        assert second == 2880.0


class TestIngestWritesGameJson:
    def test_game_json_written_after_successful_ingest(
        self, tmp_storage: LocalStorage
    ) -> None:
        from ingest import _run_ingest

        fake_metadata = {
            "video_id": "vid1",
            "video_filename": "match.mp4",
            "duration_seconds": 5400.0,
            "fixture_id": 99,
            "workspace": str(tmp_storage.workspace_path("vid1")),
        }
        fake_fixture_row = {
            "home_team": "Liverpool", "away_team": "Man City",
            "league": "Premier League", "date": "2024-10-26",
        }
        fake_transcription = {
            "kickoff_first_half": 330.0, "kickoff_second_half": 3420.0,
            "utterances": [],
        }
        fake_aligned = {"video_id": "vid1", "event_count": 0, "events": []}

        with (
            patch("ingest.download_and_save", return_value=fake_metadata),
            patch("ingest.resolve_fixture_for_video", return_value=MagicMock(
                fixture_id=99, fixture_row=fake_fixture_row, teams_parsed=True,
                team_a="Liverpool", team_b="Man City",
            )),
            patch("ingest.fetch_match_events", return_value={"events": [], "event_count": 0}),
            patch("ingest.transcribe", return_value=fake_transcription),
            patch("ingest.align_events", return_value=fake_aligned),
        ):
            _run_ingest(
                "https://www.youtube.com/watch?v=vid1",
                storage=tmp_storage,
                confirm_kickoffs_fn=lambda a, b: (330.0, 3420.0),
            )

        game_data = tmp_storage.read_json("vid1", "game.json")
        assert game_data["home_team"] == "Liverpool"
        assert game_data["kickoff_first_half"] == 330.0
        assert game_data["source"] == "https://www.youtube.com/watch?v=vid1"

    def test_game_json_not_written_on_failure(
        self, tmp_storage: LocalStorage
    ) -> None:
        from ingest import _run_ingest

        with patch("ingest.download_and_save", side_effect=Exception("download failed")):
            with pytest.raises(Exception, match="download failed"):
                _run_ingest(
                    "https://www.youtube.com/watch?v=vid1",
                    storage=tmp_storage,
                    confirm_kickoffs_fn=lambda a, b: (0.0, 0.0),
                )

        assert not tmp_storage.local_path("vid1", "game.json").exists()
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_ingest.py -v
```
Expected: `ModuleNotFoundError: No module named 'ingest'`

- [ ] **Step 3: Create `ingest.py`**

**Important:** `ingest.py` must NOT import anything from `main.py` — after Task 12
rewrites `main.py`, those imports would break. All helpers (`_pick_youtube_result`,
fixture resolution logic) belong in `ingest.py` itself. Copy `_pick_youtube_result`
and `_resolve_fixture_auto` from the current `main.py` into `ingest.py`, then remove
them from `main.py` during Task 12.

```python
"""Ingest entrypoint — one-time preprocessing script per game."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from config.settings import PIPELINE_WORKSPACE
from models.game import GameState
from pipeline.event_aligner import EventAlignerError, align_events
from pipeline.match_events import MatchEventsError, fetch_match_events
from pipeline.match_finder import (
    MatchFinderError,
    download_and_save,
    extract_video_id_from_url,
    find_match,
    is_url,
    resolve_fixture_for_video,
)
from pipeline.transcription import TranscriptionError, transcribe
from utils.logger import setup_logging
from utils.storage import LocalStorage, StorageBackend

# Type alias for the injectable kickoff confirmation function
ConfirmKickoffsFn = Callable[[float | None, float | None], tuple[float, float]]


def _parse_timestamp(raw: str) -> float | None:
    """Parse mm:ss or raw seconds string to float seconds."""
    raw = raw.strip()
    if ":" in raw:
        parts = raw.split(":")
        try:
            return int(parts[0]) * 60 + int(parts[1])
        except (ValueError, IndexError):
            return None
    try:
        return float(raw)
    except ValueError:
        return None


def _confirm_kickoffs_interactive(
    auto_first: float | None,
    auto_second: float | None,
) -> tuple[float, float]:
    """Interactive kickoff confirmation. Loops until valid timestamps entered."""

    def _confirm_one(label: str, auto: float | None) -> float:
        if auto is not None:
            mins, secs = divmod(int(auto), 60)
            print(f"  {label} kickoff detected at {mins}:{secs:02d} — correct? [Y/n] ", end="")
            answer = input("").strip().lower()
            if answer in ("", "y", "yes"):
                return auto
        else:
            print(f"  Could not auto-detect {label} kickoff.")

        while True:
            raw = input(f"  Enter {label} kickoff time (mm:ss or seconds): ").strip()
            ts = _parse_timestamp(raw)
            if ts is not None:
                return ts
            print("  Invalid format. Try e.g. '5:30' or '330'.")

    first = _confirm_one("first half", auto_first)
    second = _confirm_one("second half", auto_second)
    return first, second


def _run_ingest(
    url: str,
    *,
    storage: StorageBackend,
    confirm_kickoffs_fn: ConfirmKickoffsFn = _confirm_kickoffs_interactive,
) -> None:
    """Core ingest logic. Separated from CLI for testability."""

    # 1. Download video
    print("\n[1/5] Downloading video...")
    metadata = download_and_save(url, fixture_id=None, skip_duration_check=False, storage=storage)
    video_id: str = metadata["video_id"]
    source = f"https://www.youtube.com/watch?v={video_id}"
    print(f"       Video ID: {video_id} ({metadata['duration_seconds'] / 60:.0f} min)")

    # Resolve fixture (uses local _resolve_fixture_and_row — no import from main.py)
    fixture_id, fixture_row = _resolve_fixture_and_row(
        metadata.get("video_filename", ""),
    )
    if fixture_id:
        metadata["fixture_id"] = fixture_id

    # 2. Fetch events
    print("\n[2/5] Fetching match events from API-Football...")
    match_events = fetch_match_events(metadata, storage)
    print(f"       {match_events['event_count']} events retrieved")

    # 3. Transcribe
    print("\n[3/5] Transcribing commentary...")
    transcription = transcribe(metadata, storage)
    print(f"       {len(transcription.get('utterances', []))} utterances")

    # 4. Confirm kickoffs (BEFORE alignment)
    print("\n[4/5] Confirming kickoff timestamps...")
    kickoff_first, kickoff_second = confirm_kickoffs_fn(
        transcription.get("kickoff_first_half"),
        transcription.get("kickoff_second_half"),
    )

    # 5. Align events using confirmed kickoffs
    print("\n[5/5] Aligning events to video timestamps...")
    align_events(match_events, metadata, storage, kickoff_first, kickoff_second)

    # Write game.json
    home = (fixture_row or {}).get("home_team", "")
    away = (fixture_row or {}).get("away_team", "")
    league = (fixture_row or {}).get("league", "")
    date = (fixture_row or {}).get("date", "")[:10] if fixture_row else ""

    game = GameState(
        video_id=video_id,
        home_team=home,
        away_team=away,
        league=league,
        date=date,
        fixture_id=int(metadata.get("fixture_id") or 0),
        video_filename=metadata["video_filename"],
        source=source,
        duration_seconds=metadata["duration_seconds"],
        kickoff_first_half=kickoff_first,
        kickoff_second_half=kickoff_second,
    )
    storage.write_json(video_id, "game.json", game.to_dict())
    print(f"\n  Game ingested — ready for queries.")
    print(f"  {home} vs {away} | {league} | {date}\n")


def _pick_youtube_result(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Show YouTube search results and let the user pick one.
    (Copied from original main.py — remove it from main.py in Task 12.)
    """
    if not candidates:
        print("  No full-match videos found.")
        return None
    print(f"\n  Found {len(candidates)} full-match candidate(s):\n")
    for i, c in enumerate(candidates, 1):
        secs = int(c["duration_seconds"])
        dur = f"{secs // 3600}h{(secs % 3600) // 60:02d}m" if secs >= 3600 else f"{secs // 60}m{secs % 60:02d}s"
        print(f"  [{i}] {c['title']}\n      Duration: {dur}  |  {c['url']}\n")
    choice = input(f"  Pick a video [1-{len(candidates)}], or 's' to skip: ").strip()
    if choice.lower() == "s":
        return None
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(candidates):
            return candidates[idx]
    except ValueError:
        pass
    return candidates[0]


def _resolve_fixture_and_row(
    video_title: str,
    upload_year: int | None = None,
) -> tuple[int | None, dict[str, Any] | None]:
    """Attempt auto-resolution of fixture; return (fixture_id, fixture_row).
    Calls resolve_fixture_for_video from match_finder — no import from main.py.
    """
    try:
        res = resolve_fixture_for_video("", video_title, upload_year=upload_year)
        if res.fixture_id and res.fixture_row:
            return res.fixture_id, res.fixture_row
        if res.fixture_id:
            return res.fixture_id, None
    except Exception:
        pass
    return None, None


def run() -> None:
    """CLI entrypoint for ingest."""
    setup_logging()
    print("\n  Football Highlights — Ingest")
    print("  " + "-" * 30)
    print("  Enter a YouTube URL or match search query.\n")

    try:
        user_input = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)

    if not user_input:
        print("  Nothing entered.")
        return

    storage = LocalStorage(root=PIPELINE_WORKSPACE)

    # If text query, find the video URL first
    url = user_input
    if not is_url(user_input):
        result = find_match(user_input)
        candidates = result.get("candidates", [])
        chosen = _pick_youtube_result(candidates)
        if not chosen:
            print("  Cancelled.")
            return
        url = chosen["url"]

    try:
        _run_ingest(url, storage=storage)
    except KeyboardInterrupt:
        print("\nCancelled.")
    except (MatchFinderError, MatchEventsError, TranscriptionError, EventAlignerError) as exc:
        print(f"\n  Error: {exc}\n", file=sys.stderr)


if __name__ == "__main__":
    run()
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_ingest.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add ingest.py tests/test_ingest.py
git commit -m "feat: add ingest.py one-time preprocessing entrypoint"
```

---

## Task 12: Rewrite main.py as Query REPL

**Files:**
- Rewrite: `main.py`
- Modify: `tests/test_main.py`

- [ ] **Step 1: Write failing tests for main REPL helpers**

In `tests/test_main.py`, add/replace tests for the new REPL flow:
```python
from models.game import GameState
from utils.storage import LocalStorage

def _make_game(video_id: str = "vid1") -> GameState:
    return GameState(
        video_id=video_id, home_team="Liverpool", away_team="Man City",
        league="Premier League", date="2024-10-26", fixture_id=1,
        video_filename="match.mp4",
        source=f"https://www.youtube.com/watch?v={video_id}",
        duration_seconds=5400.0, kickoff_first_half=330.0, kickoff_second_half=3420.0,
    )

class TestDisplayGameList:
    def test_formats_game_line(self, capsys: pytest.CaptureFixture[str]) -> None:
        from main import _display_game_list
        _display_game_list([_make_game()])
        captured = capsys.readouterr()
        assert "Liverpool" in captured.out
        assert "Man City" in captured.out
        assert "2024-10-26" in captured.out

class TestNoGamesReady:
    def test_exits_cleanly_when_no_games(
        self, tmp_storage: LocalStorage, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]
    ) -> None:
        from main import run
        monkeypatch.setattr("main._make_storage", lambda: tmp_storage)
        monkeypatch.setattr("builtins.input", lambda _: "quit")
        run()
        out = capsys.readouterr().out
        assert "No ingested games" in out or "no games" in out.lower()
```

- [ ] **Step 2: Run to see current state**

```bash
pytest tests/test_main.py -v
```

- [ ] **Step 3: Rewrite `main.py`**

```python
"""Query REPL — pick an ingested game and generate highlights from natural language."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from config.settings import PIPELINE_WORKSPACE
from models.events import AlignedEvent
from models.game import GameState
from models.highlight_query import HighlightQuery, QueryType
from pipeline.clip_builder import ClipBuilderError, build_highlights
from pipeline.event_filter import filter_events
from pipeline.query_interpreter import QueryInterpreterError, interpret_query
from utils.game_registry import GameRegistry
from utils.logger import setup_logging
from utils.storage import LocalStorage, StorageBackend


def _make_storage() -> LocalStorage:
    return LocalStorage(root=PIPELINE_WORKSPACE)


def _prompt(msg: str, default: str = "") -> str:
    try:
        value = input(msg).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return value or default


def _display_game_list(games: list[GameState]) -> None:
    print()
    for i, g in enumerate(games, 1):
        print(f"  [{i}] {g.home_team} vs {g.away_team}  |  {g.league}  |  {g.date}")
    print()


def _load_aligned_events(game: GameState, storage: StorageBackend) -> list[AlignedEvent]:
    data = storage.read_json(game.video_id, "aligned_events.json")
    return [AlignedEvent.from_dict(e) for e in data.get("events", [])]


def _game_repl(game: GameState, storage: StorageBackend) -> None:
    """Inner REPL for a single chosen game."""
    print(f"\n  {game.home_team} vs {game.away_team} — {game.date}")
    print("  Type your highlights request, 'back' to pick another game, or 'quit'.\n")

    aligned_events = _load_aligned_events(game, storage)

    while True:
        raw = _prompt("> ")
        if raw.lower() in ("quit", "exit", "q"):
            print("Bye!")
            sys.exit(0)
        if raw.lower() == "back":
            return
        if not raw:
            continue

        try:
            query = interpret_query(raw, game, aligned_events)
        except QueryInterpreterError as exc:
            print(f"  Error: {exc}", file=sys.stderr)
            continue

        print(f"  Understood: {query.query_type.value}", end="")
        if query.event_types:
            print(f" — {', '.join(et.value for et in query.event_types)}", end="")
        if query.player_name:
            print(f" — {query.player_name}", end="")
        print()

        filtered = filter_events(aligned_events, query)

        try:
            result = build_highlights(filtered, game, query, storage)
        except ClipBuilderError as exc:
            print(f"  Error building highlights: {exc}", file=sys.stderr)
            continue

        print(f"\n  Done! {Path(result['highlights_path']).name}")
        print(f"    {result['clip_count']} clips | {result['total_duration_display']} total\n")


def run() -> None:
    """Main query REPL."""
    setup_logging()
    storage = _make_storage()
    registry = GameRegistry(storage)

    games = registry.list_ready()
    if not games:
        print("\n  No ingested games found.")
        print("  Run 'python ingest.py' first to preprocess a match.\n")
        return

    print("\n  Football Highlights Generator")
    print("  " + "-" * 34)

    while True:
        _display_game_list(games)
        pick = _prompt(f"  Pick a game [1-{len(games)}] or 'quit': ")
        if pick.lower() in ("quit", "exit", "q"):
            print("Bye!")
            break
        try:
            idx = int(pick) - 1
            if 0 <= idx < len(games):
                _game_repl(games[idx], storage)
            else:
                print("  Invalid choice.")
        except ValueError:
            print("  Please enter a number.")


if __name__ == "__main__":
    run()
```

- [ ] **Step 4: Remove helpers that moved to `ingest.py` from old `main.py`**

When rewriting `main.py`, delete `_pick_youtube_result`, `_resolve_fixture_auto`,
`_link_fixture_interactive`, `_pick_fixture_from_team_search`,
`_pick_fixture_from_list`, `_step1_get_video`, and `_handle_query` — these all
belong to the old end-to-end flow and are either gone or live in `ingest.py` now.

- [ ] **Step 5: Update / fix `tests/test_main.py`**

Remove tests that reference the old main.py entrypoint logic (YouTube search, download flow — those are now in `ingest.py`). Keep / update tests for helpers like `_display_game_list`. Add `_make_storage` monkeypatching where needed.

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_main.py -v && pytest -x -q
```
Expected: all pass

- [ ] **Step 7: Run full suite**

```bash
pytest --tb=short -q
```
Expected: all tests pass, no regressions

- [ ] **Step 8: Final commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat: rewrite main.py as query REPL — completes pipeline refactor"
```

---

## Verification

After all tasks complete:

```bash
# Full test suite
pytest -v

# Lint + type checks
ruff check .
mypy .

# Smoke test ingest (requires real API keys in .env)
# python ingest.py

# Smoke test query REPL
# python main.py
```
