# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

Automatic Football Highlights Generator — takes a full match video (~90 min) and produces a highlights clip using **API-Football match events** (goals, cards, VAR, etc.) aligned to the video via **transcription-based kickoff detection** and **utterance refinement**, then **FFmpeg** cutting and concatenation.

**Primary path:** API tells *what* happened and when (match minute); transcription tells *where* that is in the video (kickoff anchors + nearest utterance). The older LLM excitement pipeline (`excitement.py`, `edr.py`, `filtering.py`) remains in the repo but is not used by `main.py`.

**Fixture linking (`main.py`):** After a YouTube pick, **`resolve_fixture_for_video`** parses teams from the video title, calls **`/fixtures/headtohead`**, and filters by year (from the user query) and **Champions League** when the query mentions it. Manual **`search_fixtures`** / fixture ID remain as fallbacks.

## Commands

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pre-commit install

# Run (interactive CLI — no CLI flags)
python main.py

# Run tests
pytest                          # all tests with coverage
pytest tests/test_ingestion.py  # single file
pytest -v -k "test_name"        # single test

# Lint/type-check (also runs automatically on commit)
ruff check .
mypy .
bandit -r . -c pyproject.toml
```

**Environment:** Requires `.env` with:

- `ASSEMBLYAI_API_KEY=<key>` — transcription (AssemblyAI)
- `API_FOOTBALL_KEY=<key>` — [API-Football](https://www.api-football.com/) via `v3.football.api-sports.io` (RapidAPI header auth). Free tier has a **recent-dates-only** restriction; historical fixtures may need a paid plan.
- Optional (legacy / unused by default path): `AZURE_OPENAI_*`, `OPENAI_API_KEY` — old excitement stages

## Architecture

Cached outputs live under `pipeline_workspace/<video_id>/` so expensive steps (download, APIs, FFmpeg) can be skipped on re-run.

**Current pipeline (API-driven):**

```
Text query or URL → match_finder → match_events → transcription (+ kickoffs)
    → event_aligner → clip_builder → highlights.mp4
```

| Stage | Module | Output (typical) |
|-------|--------|------------------|
| 1 | `pipeline/match_finder.py` | `metadata.json`, downloaded `*.mp4` |
| 2 | `pipeline/match_events.py` | `match_events.json` |
| 3 | `pipeline/transcription.py` | `transcription.json` (+ `kickoff_first_half`, `kickoff_second_half`) |
| 4 | `pipeline/event_aligner.py` | `aligned_events.json` |
| 5 | `pipeline/clip_builder.py` | `clip_manifest.json`, `clips/`, `highlights.mp4` |

**Config:**

- `config/settings.py` — paths, API keys, `DEFAULT_HIGHLIGHTS_DURATION_SECONDS`, `MERGE_GAP_SECONDS`
- `config/clip_windows.py` — per-`EventType` pre/post roll and priority for budget trimming
- `models/events.py` — `MatchEvent`, `AlignedEvent`, `EventType`

**Utilities:** `utils/ffmpeg.py`, `utils/logger.py`

**Commentator identification** (`transcription.py`): speakers above `COMMENTATOR_TIME_RATIO` of the top speaker’s talk time are commentators.

## Testing

- `tests/conftest.py`: `tmp_workspace` monkeypatches `PIPELINE_WORKSPACE` per module; `fake_ffprobe_duration` mocks FFmpeg
- New pipeline modules are covered with unit tests; mock external I/O (yt-dlp, HTTP, AssemblyAI)

## Code Conventions

- Python 3.12, type hints required everywhere (`mypy` enforces `disallow_untyped_defs = true`)
- Line length: 100 characters (`ruff`)
- Each pipeline module raises its own exception class (e.g., `MatchFinderError`, `TranscriptionError`)
- Use `monkeypatch` / `unittest.mock` for external dependencies in tests (yt-dlp, AssemblyAI, FFmpeg, HTTP)
