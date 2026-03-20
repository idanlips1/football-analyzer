# Football Highlights Generator

Builds a highlights video from a full football match by combining **[API-Football](https://www.api-football.com/)** event data (goals, cards, VAR, etc.) with **commentary transcription** to align match minutes to video time, then cuts and merges clips with **FFmpeg**.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pre-commit install
```

## Environment

Create a `.env` file in the project root (see `.gitignore` — never commit secrets):

| Variable | Purpose |
|----------|---------|
| `ASSEMBLYAI_API_KEY` | Transcribe match audio (AssemblyAI) |
| `API_FOOTBALL_KEY` | Fetch fixtures/events from `v3.football.api-sports.io` (same key as RapidAPI / API-Sports) |

**API-Football free tier:** daily request limits apply, and **fixture data is often limited to a short rolling window of dates** (check your plan on [API-Football](https://www.api-football.com/)). For older matches you may need a paid tier or manual workflow.

**Fixture ID:** The interactive flow can ask for a **fixture ID** so events match the video. You can find it in the API dashboard or via the API (e.g. fixtures by date/league).

## Usage

Interactive CLI only (no command-line arguments):

```bash
python main.py
```

You can describe a match (YouTube search for full-length uploads) or paste a **YouTube URL**. You’ll pick a video, optionally enter a fixture ID, then the pipeline downloads, transcribes, aligns events, and writes `pipeline_workspace/<video_id>/highlights.mp4`.

## Workspace

Downloaded videos, JSON caches, and `highlights.mp4` are stored under `pipeline_workspace/` (ignored by git except `.gitkeep`).

## Testing

```bash
pytest
```

## Code style

Type annotations on all functions. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Static analysis

Runs on commit via pre-commit (ruff, mypy, bandit). Manual run:

```bash
ruff check .
mypy .
bandit -r . -c pyproject.toml
```

## Legacy pipeline

Modules such as `pipeline/excitement.py`, `pipeline/edr.py`, and `pipeline/filtering.py` implement an older audio/LLM-based path; the default `main.py` flow uses the API-driven pipeline above.
