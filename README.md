# Football Highlights Generator

Builds a highlights video from a full football match by combining **[API-Football](https://www.api-football.com/)** event data (goals, cards, VAR, etc.) with **commentary transcription** to align match minutes to video time, then cuts and merges clips with **FFmpeg**.

## Prerequisites

| Dependency | Why | Install |
|------------|-----|---------|
| **Python 3.12+** | Runtime | [python.org](https://www.python.org/downloads/) |
| **FFmpeg** | Video download (yt-dlp merge), audio extraction, clip cutting & concatenation | `brew install ffmpeg` (macOS) · `sudo apt install ffmpeg` (Ubuntu) · [ffmpeg.org](https://ffmpeg.org/download.html) |

Verify FFmpeg is available:

```bash
ffmpeg -version
```

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

**Fixture linking (automatic):** After you pick a YouTube result, the app **parses the two teams from the video title** (e.g. `Liverpool v Real Madrid …`) and calls API-Football **head-to-head** for those clubs. It uses your **search text** (e.g. `Champions League final 2024`) to pick the **calendar year** and, when possible, narrow to **UEFA Champions League** so the right fixture is chosen without typing an ID.

If there is **no clear match**, **several matches** in that year, or the title **does not list two teams**, you get a **numbered list** to choose from, or **manual options** (`[i]` fixture ID, `[s]` team search, `[Enter]` skip).

## Usage

Interactive CLI only (no command-line arguments):

```bash
source .venv/bin/activate   # if needed
python main.py
```

You can describe a match (YouTube search for full-length uploads) or paste a **YouTube URL**. The fixture is linked **automatically** when the title lists two teams (see above); otherwise you may pick from a list or use manual options. Then wait for download + transcription, confirm or enter kickoff times if prompted, and get `pipeline_workspace/<video_id>/highlights.mp4`.

### Testing on a real match

1. **Environment:** `.env` must include valid `ASSEMBLYAI_API_KEY` and `API_FOOTBALL_KEY`. FFmpeg must be installed and on your `PATH` (used for duration and cutting).
2. **Pick something your API plan can see:** On a **free** plan, fixtures/events are often only available for **recent** dates. Choose a finished game from the last day or two, or use a **paid** plan for older matches.
3. **Align video and data:** The YouTube full match should be the **same fixture** as the API fixture (same teams and competition). Wrong pairing = wrong minute→time mapping.
4. **Run:** `python main.py` → describe the match or paste a URL → pick a full-match video → the app **resolves the fixture** from the title + your search text when possible → wait (download + AssemblyAI are slow).
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
