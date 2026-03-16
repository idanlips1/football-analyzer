# Final Project — Automatic Football Highlights Generator

## Overview

In this project you will build a **single-service pipeline** that takes a full football match video (~90 minutes) and automatically produces a short highlights clip (~2 minutes). The core idea is simple: **the commentator is your highlight detector**. When something exciting happens in a football match — a goal, a dangerous counter-attack, a great save, a red card — the commentator reacts. By transcribing the commentary, analyzing the commentator's vocal energy and language, and classifying each segment with an LLM, you can build an **EDR (Event Detection Report)**: a structured timeline of detected events with confidence scores. The EDR is then used to cut and stitch the most exciting segments into a final highlights video.

Crucially, the user should be able to **choose which types of events** they want in their highlights. One user might want "just goals and red cards", another might want "all saves and counter-attacks." The pipeline always builds the full EDR with every detected event, and then filters it based on the user's preferences before cutting the video. This means the expensive analysis work (transcription, LLM calls) only runs once per match — subsequent requests for different event types just re-filter the same EDR and re-cut the video.

The pipeline is designed to be **extensible** — the commentator analysis is the core signal for now, but the architecture should make it easy to plug in additional signals in the future (e.g., crowd noise analysis, visual scene detection, etc.) without rewriting the whole system.

This project combines techniques you've used in previous exercises — speaker diarization (therapy session exercise), audio processing, and LLM-based analysis — into one cohesive pipeline.

The project can be a CLI or an API with no GUI if you wish.

---

## Where to Get Match Videos

You can download sample full-match footage from YouTube. For example, search for:
- `https://www.youtube.com/results?search_query=full+football+match+90+minutes`
- `https://www.youtube.com/results?search_query=full+soccer+match+highlights+included`

Use `yt-dlp` (https://github.com/yt-dlp/yt-dlp) to download videos, as you did in the video search exercise.

**Important:** During development, work with short clips (5–10 minutes) to save time and resources. Only test on a full 90-minute match when the pipeline is working end-to-end.

---

## The Pipeline — Step by Step

Your program should be a **single Python service** that runs the following pipeline stages sequentially. Each stage should save its intermediate output to disk (so that if you run the pipeline again on the same video, completed stages can be skipped — just like the caching logic you implemented in the video search exercise).

### Stage 1 — Video Ingestion & Metadata Extraction

The user provides a path to a local football match video file (or a YouTube URL to download with `yt-dlp`).

- Extract basic metadata: duration, resolution, FPS using `ffprobe` (part of FFmpeg).
- Validate that the video is at least 20 minutes long (reject short clips that aren't real matches).
- Save the raw video to a `pipeline_workspace/<video_id>/` folder.

### Stage 2 — Audio Extraction & Commentator Transcription

Extract the audio and use speaker diarization to isolate the commentator's speech — this is the foundation of your entire highlight detection.

- Extract the audio track from the video using FFmpeg (convert to WAV or MP3).
- Use **AssemblyAI** (https://www.assemblyai.com/) to transcribe the audio with **speaker diarization** enabled (as you did in the therapy session exercise). This will separate different speakers in the audio.
- Identify which speaker is the **commentator** — the speaker with the most total speaking time is almost certainly the main commentator. If there are two commentators (main + co-commentator), you can treat both as "commentator" speakers.
- Save the full transcription with speaker labels and timestamps as a JSON file.
- **Cache the AssemblyAI transcription** to disk so re-runs don't re-transcribe (transcription of a 90-minute video is expensive and slow).

### Stage 3 — Commentator Excitement Analysis

Now analyze the commentator's speech to find the exciting moments. Use two complementary signals:

**Signal A — Vocal energy analysis:**
- Use `librosa` (https://librosa.org/) to analyze the audio energy during the commentator's speaking segments.
  - For each segment where the commentator is speaking (from the diarization timestamps), compute the **RMS energy** (root mean square).
  - Normalize energy values relative to the commentator's **baseline** speaking level (their average energy across the match). This way you're detecting when the commentator is louder *than usual*, not just loud in absolute terms.
  - Detect **spikes** — segments where the commentator's energy is significantly above their baseline (e.g., above the 85th percentile).

**Signal B — Text/language analysis:**
- Analyze the transcribed text to detect excitement through language. You can combine two approaches:
  - **Keyword detection:** Look for exclamatory words and football-specific terms that indicate important moments: "GOAL!", "scores!", "incredible", "what a save", "penalty", "red card", "offside", "chance", "dangerous", "brilliant", "unbelievable", etc. Build a configurable keyword list with associated weights (e.g., "goal" has higher weight than "corner").
  - **LLM-based classification:** Send the commentator's text segments (grouped in ~30-second windows) to an LLM and ask it to classify the excitement level and event type. The LLM should return:
    - **Event type** — one of a predefined list: `[goal, shot_on_target, save, foul, card, corner, free_kick, counter_attack, celebration, penalty, var_review, other]`
    - **Excitement score** — a value from 1–10 indicating how highlight-worthy this moment is.
    - **Description** — a short summary of what's happening based on the commentary text.
  - Cache all LLM responses to disk (JSON) so re-runs don't waste API calls.

- Save the commentator excitement analysis as a JSON file: for each detected exciting segment, include the timestamp range, the transcribed text, the vocal energy score, the keyword matches, and the LLM classification.

### Stage 4 — EDR (Event Detection Report) Generation

Combine the vocal energy and text analysis signals into a single **Event Detection Report** — a structured JSON document that represents the timeline of detected events.

The EDR should be a list of entries, each containing:
```
{
    "timestamp_start": "00:23:12",
    "timestamp_end": "00:23:42",
    "commentator_energy": 0.92,
    "commentator_text": "And he scores! What an incredible strike from outside the box!",
    "keyword_matches": ["scores", "incredible", "strike"],
    "event_type": "goal",
    "llm_description": "Long-range goal scored from outside the penalty area",
    "llm_excitement_score": 9,
    "final_score": 9.3,
    "include_in_highlights": true
}
```

To build the EDR:
- Merge the vocal energy spikes with the text analysis results. Moments where **both** the commentator's voice is excited AND the text contains high-excitement language should get higher final scores.
- Compute a **final score** for each event by combining (with configurable weights):
  - Commentator vocal energy score.
  - LLM excitement score.
  - Event type priority (goals > shots on target > saves > cards > fouls, etc.).
  - Keyword match strength.
- Sort events by final score and select the top N events that fit within the target highlights duration (~2 minutes).
- For each selected event, define a **clip window**: use the commentator's speech segment timestamps as the natural boundaries, then add a buffer of a few seconds before and after (to capture visual context — the action usually starts slightly before the commentator reacts). Experiment with buffer durations.
- Handle **overlapping clips** — if two events are very close together, merge them into one longer clip instead of having two separate cuts.

Save the **full EDR** (all detected events, regardless of type) as a formatted JSON file. This is the complete analysis of the match — it only needs to be generated once.

### Stage 4b — Event Filtering (User Preferences)

Before cutting the video, filter the EDR based on the **user's requested event types**. The user should be able to specify which events they want in their highlights, for example:

- `python main.py --input match.mp4 --events goal,card,save` — only goals, cards, and saves.
- `python main.py --input match.mp4 --events all` — everything (default).
- `python main.py --input match.mp4 --events goal` — a "just the goals" compilation.

The available event types are the same ones the LLM classifies: `goal, shot_on_target, save, foul, card, corner, free_kick, counter_attack, celebration, penalty, var_review, other`.

This stage takes the full EDR, filters it by the requested event types, then applies the duration limit and overlap merging to produce a **filtered EDR** for video cutting. This means that if the user already ran the pipeline on a match and now wants a different selection of events, **stages 1–4 are skipped entirely** (everything is cached) and only the filtering + video cutting re-runs. This is fast — no re-transcription, no re-analysis, no wasted LLM calls.

### Stage 5 — Video Cutting & Highlights Assembly

Use FFmpeg (via `ffmpeg-python` or raw subprocess calls) to produce the final highlights video.

- For each clip defined in the EDR:
  - Cut the segment from the original video with the exact timestamps.
  - Optionally add a short **text overlay** showing the event type and match timestamp (e.g., "GOAL — 67:23") using FFmpeg's `drawtext` filter.
- Concatenate all clips into a single highlights video.
- Optionally add a short **fade transition** (0.5s crossfade) between clips for a more polished result.
- Save the final highlights video to `pipeline_workspace/<video_id>/highlights.mp4`.

---

## Extensibility — Future Signals

The pipeline is built around the commentator as the sole detection signal, but the EDR architecture is designed so that **additional signals can be plugged in later** without restructuring the whole system. Each signal just needs to produce a list of timestamped events with scores, and the EDR merger combines them. Potential future additions:

- **Crowd noise analysis:** Use `librosa` to analyze the full audio track's RMS energy for crowd roar spikes, independent of the commentator.
- **Visual scene detection:** Use `pyscenedetect` to detect rapid camera cuts (which often indicate replays and exciting moments).
- **Multimodal LLM visual analysis:** Extract keyframes at candidate timestamps and send them to Gemini or Moondream for visual event classification.

You don't need to implement these — just make sure your EDR generation logic can accept events from multiple sources and merge them.

---

## Technical Requirements

### General

- All code should be in **Python**.
- The project can be exposed as a **CLI** or as a **FastAPI-based API** (no GUI required). If CLI:
  ```
  python main.py --input match.mp4 --duration 120 --events goal,card,save
  ```
  Where `--duration` is the target highlights length in seconds (default: 120) and `--events` is a comma-separated list of event types to include (default: `all`).
  If API, an endpoint that accepts a video file or URL, a target duration, and an event type filter, and returns the highlights path.
- Use **proper logging** throughout the pipeline (Python's `logging` module). Each stage should log its start, end, and key decisions (e.g., "Transcription complete — identified 3 speakers, commentator is Speaker A", "Found 18 excitement spikes", "LLM classified segment at 23:15 as 'goal' with excitement 9").
- Implement **caching/checkpointing** — if a stage has already been completed for a given video, skip it on re-run. Use marker files or check for the existence of output files.
- Include a `requirements.txt` with all dependencies.
- Include a `README.md` explaining how to set up and run the project.

### Testing

- You must have **proper unit tests** with real, meaningful assertions — not trivial tests or tests that don't actually verify behavior. Use `pytest`.
- Aim for **proper code coverage**. Use `pytest-cov` to measure and report coverage. Focus coverage on your core logic (scoring, merging, clip selection, commentator identification) rather than on FFmpeg/API wrapper code.
- At least one class or method must be developed using **TDD (Test-Driven Development)**: write the tests first, see them fail, then write the implementation to make them pass. A good candidate for TDD is the EDR scoring/merging logic in Stage 4 — it's pure logic with clear inputs and outputs. Document which part you did with TDD in your README.
- Examples of **good tests** for this project:
  - Given a diarization result with 3 speakers, does the commentator identifier correctly pick the one with the most speaking time?
  - Does the keyword detector find "goal" and "incredible" in a segment and assign the correct weights?
  - Does the scoring formula rank a "goal" with high vocal energy above a "corner" with low energy?
  - Does the event filter correctly keep only "goal" and "card" events when those are requested?
  - Does the event filter return all events when `--events all` is used?
  - Does the clip overlap handler correctly merge two events that are 3 seconds apart?
  - Does the highlight duration selector correctly pick events that fit within the target length?
  - Does the video metadata validator reject a 5-minute clip?
- Examples of **bad/trivial tests** (do not do these):
  - Testing that `True == True`.
  - Testing that a function exists.
  - Testing that a JSON file can be opened.

### Static Code Analysis

- You must run **static code analyzers** on your codebase to catch issues. Recommended tools:
  - `ruff` (fast Python linter) or `flake8` for style/lint checks.
  - `mypy` for type checking (add type hints to your core modules).
  - `bandit` for security checks (optional but recommended).
- Configure the analyzer to **run on every commit** using a pre-commit hook. Use the `pre-commit` framework (https://pre-commit.com/) with a `.pre-commit-config.yaml` file in your repo.

### Azure Hosting

Your project (or at least a part of it) must be **hosted on Azure**. Since free Azure instances are quite weak (limited CPU/RAM, no GPU), you should **not** deploy the entire pipeline there — video processing and transcription are too heavy.

Good candidates for Azure deployment:
- **The API layer** — a lightweight FastAPI container that accepts requests, triggers the pipeline (or returns results from a pre-processed EDR), and serves the highlights video or EDR JSON. This is just a thin HTTP layer and doesn't need heavy resources.
- **The EDR query service** — a small service that takes a pre-built EDR JSON and lets users query it: list events, filter by type, get the highlights for a specific duration.

Deploy using an **Azure Container Instance** or **Azure App Service** with a Docker container. Include a `Dockerfile` in your repo.

---

## Deliverables & Checkpoints

Break your development into these checkpoints. **Test each one before moving on.** Make multiple small commits during the work process.

**Checkpoint 1 — Scaffolding & Ingestion**
- Project structure is set up (folders, `requirements.txt`, logging config, pre-commit hooks, `Dockerfile`).
- Static analyzer is configured and runs on every commit.
- Can accept a video file (or YouTube URL), download it, extract metadata, and save it to the workspace folder.
- Basic CLI/API argument parsing works.
- First unit tests are written (e.g., metadata validation).

**Checkpoint 2 — Transcription & Speaker Identification**
- Audio extraction from video works.
- AssemblyAI transcription with speaker diarization works on a short test clip.
- Commentator is correctly identified (speaker with most speaking time).
- Transcription results are cached to disk.
- Unit tests for commentator identification logic.

**Checkpoint 3 — Excitement Detection**
- Vocal energy analysis detects spikes in the commentator's speaking segments.
- Keyword detection finds football-specific excitement terms.
- LLM classification returns event type and excitement score for commentary segments.
- LLM responses are cached.
- All three sub-signals produce a JSON of excited moments.
- Unit tests for keyword detection and energy spike logic.

**Checkpoint 4 — EDR Generation & Event Filtering (TDD)**
- **Build this stage using TDD** — write the scoring, merging, filtering, and clip selection tests first, then implement the logic.
- Vocal energy, keyword, and LLM signals are merged into a unified full EDR.
- Event filtering by user-requested types works correctly (e.g., `--events goal,card` keeps only those).
- Scoring, ranking, and clip selection logic works.
- Overlapping clip merging works.
- Re-running with different `--events` skips stages 1–3 and only re-filters + re-cuts.
- The EDR JSON file is human-readable and makes sense.
- Good code coverage on this module.

**Checkpoint 5 — Highlights Video**
- FFmpeg cuts and concatenates clips correctly.
- The final `highlights.mp4` plays smoothly without artifacts.
- Text overlays (if implemented) render correctly.

**Checkpoint 6 — Azure Deployment**
- A lightweight part of the project (API layer or EDR query service) is containerized and deployed to Azure.
- The deployed service is reachable and responds correctly.

**Checkpoint 7 — Full Pipeline Test & Polish**
- Run the entire pipeline end-to-end on a real full-length match.
- Verify highlights quality — do the selected moments actually look like highlights?
- Tune parameters (energy thresholds, keyword weights, LLM prompt, scoring weights, clip buffer durations) until you're happy with the output.
- Code coverage report is generated and included.
- All static analysis checks pass cleanly.

---

## Bonus Ideas (Optional, Choose At Least One)

- **Multiple highlight lengths:** Let the user choose between "short" (~1 min), "medium" (~2 min), and "long" (~5 min) highlight reels by adjusting how many events are included from the EDR. This can be exposed as a CLI flag (`--length short`) or an API parameter.
- **Crowd noise as an additional signal:** Add a new pipeline stage that analyzes the full audio track's RMS energy for crowd roar spikes (using `librosa`), independent of the commentator. Merge this into the EDR alongside the commentator signal.
- **Highlight thumbnail generator:** Automatically select the best frame from the highlights video as a thumbnail image.
- **Match summary report:** In addition to the video, generate a text/JSON summary of the match: "3 goals detected, 2 red cards, 15 exciting moments found" — like a written match report powered by the EDR.
- **Side-by-side comparison mode:** Show the user the EDR as an interactive timeline (simple HTML page) where they can see all detected events, their scores, and click to preview each clip before generating the final video.
- **Replay detection & deduplication:** Football broadcasts often show replays of the same event from multiple angles. The commentator often narrates replays too ("Let's see that again..."), which could lead to duplicates in the EDR. Detect replay-related commentary (keywords like "replay", "let's see that again", "another angle") and deduplicate events.

---

## Planning Advice

Before you start coding, take the pipeline description above and enter it into a couple of different LLMs. Ask the LLM to act as an architect and help you think through:
- What are the tricky edge cases? (e.g., what if AssemblyAI misidentifies speakers? What if the commentator is consistently monotone?)
- What parameters will need tuning?
- What's the best order to develop things in?
- How should the scoring formula work?
- Which parts of the code deserve the most test coverage?
- What should run on Azure vs. locally?

Invest real time in planning. When you are ready, start developing with an IDE connected to an LLM (VSCode with GitHub Copilot, Cursor, or similar). Make small commits at each checkpoint.
