# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automatic Football Highlights Generator — a Python pipeline that takes a full match video (~90 min) and produces a ~2-minute highlights clip by detecting commentator excitement signals (vocal energy, keywords, LLM classification).

**Core idea:** Commentator voice = highlight detector. Energy spikes + excited language → exciting moments → clip selection.

## Commands

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pre-commit install

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
- `ASSEMBLYAI_API_KEY=<key>` — Stage 2 (transcription)
- `AZURE_OPENAI_API_KEY=<key>` — Stage 3 (excitement analysis)
- `AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com` — Stage 3
- `AZURE_OPENAI_DEPLOYMENT=<deployment-name>` — Stage 3
- `AZURE_OPENAI_API_VERSION=2024-10-21` — Stage 3 (optional, defaults to `2024-10-21`)

## Architecture

The pipeline is a sequential 5-stage process. Each stage writes cached output to `pipeline_workspace/<video_id>/` so expensive re-runs (network, APIs) are skipped automatically.

```
YouTube URL → [1: ingestion] → [2: transcription] → [3: excitement] → [4: EDR] → [4b: filtering] → [5: video] → highlights.mp4
```

| Stage | Module | Status | Output File |
|-------|--------|--------|-------------|
| 1 | `pipeline/ingestion.py` | ✅ Done | `metadata.json`, `video.mp4` |
| 2 | `pipeline/transcription.py` | ✅ Done | `transcription.json` |
| 3 | `pipeline/excitement.py` | 🚧 Stub | `excitement.json` |
| 4 | `pipeline/edr.py` | 🚧 Stub | `edr.json` |
| 4b | `pipeline/filtering.py` | 🚧 Stub | `filtered_edr.json` |
| 5 | `pipeline/video.py` | 🚧 Stub | `highlights.mp4` |

**Key modules:**
- `config/settings.py` — all thresholds, paths, API keys
- `config/keywords.py` — football excitement keywords for Stage 3
- `utils/ffmpeg.py` — FFmpeg/ffprobe wrappers (audio extraction, duration probing)
- `utils/logger.py` — `get_logger(__name__)` used throughout
- `models/events.py` — EDR entry and event type data models (stub)

**Commentator identification** (`transcription.py:identify_commentators`): speaker with ≥30% of top speaker's talk time is considered a commentator (`COMMENTATOR_TIME_RATIO = 0.3` in settings).

## Testing

- `tests/conftest.py` has two key fixtures: `tmp_workspace` (isolates from real `pipeline_workspace/`) and `fake_ffprobe_duration` (mocks FFmpeg calls)
- Stages 3–5 and `models/events.py` use a TDD approach — write tests before implementation
- Coverage targets: `pipeline/`, `models/`, `config/`, `utils/`

## gstack

Use the `/browse` skill from gstack for all web browsing. Never use `mcp__claude-in-chrome__*` tools.

Available skills:
- `/plan-ceo-review` — CEO-level plan review
- `/plan-eng-review` — Engineering plan review
- `/plan-design-review` — Design plan review
- `/review` — Code review
- `/ship` — Ship a change
- `/browse` — Web browsing (use this for ALL web browsing)
- `/qa` — QA testing
- `/qa-only` — QA testing only
- `/qa-design-review` — QA + design review
- `/setup-browser-cookies` — Set up browser cookies
- `/retro` — Retrospective
- `/document-release` — Document a release

If gstack skills aren't working, run `cd .claude/skills/gstack && ./setup` to build the binary and register skills.

## Code Conventions

- Python 3.12, type hints required everywhere (`mypy` enforces `disallow_untyped_defs = true`)
- Line length: 100 characters (`ruff`)
- Each pipeline module raises its own exception class (e.g., `IngestionError`, `TranscriptionError`)
- Use `monkeypatch` / `unittest.mock` for external dependencies in tests (yt-dlp, AssemblyAI, FFmpeg)
