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

**Fixture linking:** After you choose a video, the CLI asks how to connect to API-Football:

- **`[i]`** — paste a **fixture ID** (integer) from the [API-Football dashboard](https://www.api-football.com/) or from your own API calls.
- **`[s]`** — **search by two team names** (comma-separated), optional **match date** (`YYYY-MM-DD`), and optional **season year** (the year the league season *starts*, e.g. `2025` for 2025–26). Pick a row from the list.
- **`[Enter]`** — skip (no events; the pipeline stops after download).

## Usage

Interactive CLI only (no command-line arguments):

```bash
source .venv/bin/activate   # if needed
python main.py
```

You can describe a match (YouTube search for full-length uploads) or paste a **YouTube URL**. Then link a fixture (see above), wait for download + transcription, confirm or enter kickoff times if prompted, and get `pipeline_workspace/<video_id>/highlights.mp4`.

### Testing on a real match

1. **Environment:** `.env` must include valid `ASSEMBLYAI_API_KEY` and `API_FOOTBALL_KEY`. FFmpeg must be installed and on your `PATH` (used for duration and cutting).
2. **Pick something your API plan can see:** On a **free** plan, fixtures/events are often only available for **recent** dates. Choose a finished game from the last day or two, or use a **paid** plan for older matches.
3. **Align video and data:** The YouTube full match should be the **same fixture** as the API fixture (same teams and competition). Wrong pairing = wrong minute→time mapping.
4. **Run:** `python main.py` → text search or URL → pick longest plausible full match → **`[s]`** and enter teams + optional date → pick fixture → wait (download + AssemblyAI are slow).
5. **Kickoffs:** If first/second-half kickoff isn’t detected from commentary, enter times **in seconds from the start of the file** (or `M:SS`), e.g. first whistle at `5:30` → `5:30` or `330`.
6. **Re-runs:** Outputs cache under `pipeline_workspace/<video_id>/`. Delete that folder (or specific JSON/mp4) to force a stage to re-run.

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
