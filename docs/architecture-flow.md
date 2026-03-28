# Architecture Flow

## System Overview

There are **3 entry points** and **2 pipeline phases** (ingest + query):

```
                        ENTRY POINTS
            ┌──────────────────────────────────┐
            │                                  │
   ┌────────┴────────┐  ┌──────────┐  ┌───────┴───────┐
   │   ingest.py     │  │ main.py  │  │ local_run.py  │
   │ (operator CLI)  │  │ (client) │  │  (dev runner)  │
   └────────┬────────┘  └────┬─────┘  └───────┬───────┘
            │                │                 │
            ▼                ▼                 ▼
     ┌─────────────┐  ┌───────────┐    ┌─────────────┐
     │   INGEST    │  │ Azure API │    │ QUERY-TIME  │
     │   PHASE     │  │  + Queue  │    │  PIPELINE   │
     │  (once per  │  │  + Worker │    │  (direct,   │
     │   match)    │  │           │    │   local)    │
     └─────────────┘  └─────┬─────┘    └─────────────┘
                            │
                            ▼
                     ┌─────────────┐
                     │ QUERY-TIME  │
                     │  PIPELINE   │
                     └─────────────┘
```

---

## Ingest Phase (`ingest.py` — run once per match by operator)

```
┌─────────────────────────────────────────────────────────────────────┐
│                         INGEST PIPELINE                            │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ [1/7] Download / Upload Video                               │   │
│  │       yt-dlp download OR copy local .mp4 into storage       │   │
│  │       → metadata.json, match.mp4                            │   │
│  └──────────────────────┬───────────────────────────────────────┘   │
│                         ▼                                          │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ [2/7] Transcribe (AssemblyAI)                               │   │
│  │       Full match audio → speaker-identified transcript      │   │
│  │       → transcription.json (includes kickoff timestamps)    │   │
│  └──────────────────────┬───────────────────────────────────────┘   │
│                         ▼                                          │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ [3/7] Confirm Kickoffs (interactive)                        │   │
│  │       Operator reviews/adjusts detected kickoff timestamps  │   │
│  │       → kickoff_first_half, kickoff_second_half             │   │
│  └──────────────────────┬───────────────────────────────────────┘   │
│                         ▼                                          │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ [4/7] Fixture Selection (conditional)                       │   │
│  │       IF no events_snapshot in catalog:                     │   │
│  │         Search API-Football /teams + /fixtures/headtohead   │   │
│  │         Operator picks correct fixture                      │   │
│  │       ELSE: skip (snapshot match)                           │   │
│  │       → fixture_id (int | None)                             │   │
│  └──────────────────────┬───────────────────────────────────────┘   │
│                         ▼                                          │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ [5/7] Write game.json                                       │   │
│  │       GameState with teams, kickoffs, fixture_id, duration  │   │
│  │       → game.json                                           │   │
│  └──────────────────────┬───────────────────────────────────────┘   │
│                         ▼                                          │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ [6/7] Fetch Match Events                                    │   │
│  │       Snapshot → copy bundled JSON                           │   │
│  │       fixture_id → API-Football /fixtures/events            │   │
│  │       → match_events.json                                   │   │
│  └──────────────────────┬───────────────────────────────────────┘   │
│                         ▼                                          │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ [7/7] Align Events to Video                                 │   │
│  │       Match minutes → video timestamps using kickoff anchors│   │
│  │       + utterance refinement from transcription              │   │
│  │       → aligned_events.json                                 │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                    │
│  Artifacts in pipeline_workspace/<video_id>/:                      │
│  ┌────────────────────┬────────────────────┬──────────────────────┐ │
│  │ metadata.json      │ transcription.json │ game.json            │ │
│  │ match.mp4          │ match_events.json  │ aligned_events.json  │ │
│  └────────────────────┴────────────────────┴──────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Query Phase (per user request)

### Path A: Cloud — `main.py` → API → Worker

```
┌──────────┐     POST /api/v1/jobs      ┌──────────────┐
│ main.py  │ ─────────────────────────▶ │  FastAPI      │
│ (client  │                            │  (api/app.py) │
│  REPL)   │ ◀───── poll GET /jobs/:id  │              │
└──────────┘                            └──────┬───────┘
                                               │ enqueue
                                               ▼
                                        ┌──────────────┐
                                        │  Job Queue   │
                                        │ (Azure Queue │
                                        │  or in-mem)  │
                                        └──────┬───────┘
                                               │ dequeue
                                               ▼
                                        ┌──────────────┐
                                        │   Worker     │
                                        │ (runner.py)  │
                                        └──────┬───────┘
                                               │
                                               ▼
                                     ┌─────────────────┐
                                     │ QUERY PIPELINE  │
                                     │ (see below)     │
                                     └─────────────────┘
```

### Path B: Local dev — `local_run.py`

```
┌──────────────┐
│ local_run.py │ ──── directly calls ────▶  QUERY PIPELINE
│ (CLI args or │                            (same logic,
│  interactive)│                             LocalStorage)
└──────────────┘
```

### Query Pipeline (shared by both paths)

```
┌─────────────────────────────────────────────────────────────────────┐
│               QUERY-TIME PIPELINE (catalog_pipeline.py)            │
│                    No API-Football calls here!                     │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ 1. Load Pre-processed Data                                  │   │
│  │    storage.read_json("game.json")                           │   │
│  │    storage.read_json("aligned_events.json")                 │   │
│  └──────────────────────┬───────────────────────────────────────┘   │
│                         ▼                                          │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ 2. Extract Player Names                                     │   │
│  │    Collect unique player + assist names from aligned events  │   │
│  │    → player_names: list[str]                                │   │
│  └──────────────────────┬───────────────────────────────────────┘   │
│                         ▼                                          │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ 3. LLM Interpret Query (OpenAI) ← only external call       │   │
│  │    "show me Salah's goals in the second half"               │   │
│  │    → HighlightQuery:                                        │   │
│  │        query_type: player                                   │   │
│  │        player_name: "Mohamed Salah"                         │   │
│  │        event_types: [goal]                                  │   │
│  │        minute_from: 46, minute_to: 90                       │   │
│  │        label: "salah_second_half_goals"                     │   │
│  └──────────────────────┬───────────────────────────────────────┘   │
│                         ▼                                          │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ 4. Filter Events (local, no API)                            │   │
│  │    filter by: event_type, player_name, minute_range         │   │
│  │    fuzzy player matching via difflib + substring             │   │
│  │    → filtered: list[AlignedEvent]                           │   │
│  └──────────────────────┬───────────────────────────────────────┘   │
│                         ▼                                          │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ 5. Build Highlights (FFmpeg)                                │   │
│  │    calculate_clip_windows → merge_clips → enforce_budget    │   │
│  │    cut_clip per window → concat_clips → apply_segment_fades │   │
│  │    → highlights.mp4                                         │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Data Flow Summary

```
                    INGEST (once)                          QUERY (per request)
                    ─────────────                          ──────────────────

  .mp4 file ──▶ [ingestion] ──▶ metadata.json ─────────────────────────────▶ (unused)
                    │
                    ▼
             [AssemblyAI] ──▶ transcription.json ──┐
                    │                               │
                    ▼                               │
            [confirm kickoffs]                      │
                    │                               │
                    ▼                               │
          [API-Football search] ──┐                 │
                    │             │                 │
                    ▼             ▼                 ▼
              [write game] ──▶ game.json ──────▶ load game
                    │
                    ▼
          [API-Football events                     ┌──▶ load aligned events
           or snapshot copy] ──▶ match_events.json │
                    │                              │
                    ▼                              │
            [align events] ──▶ aligned_events.json─┘
                                                   │
                                                   ▼
                                           [OpenAI interpret]
                                                   │
                                                   ▼
                                           [local filter]
                                                   │
                                                   ▼
                                            [FFmpeg cut/concat]
                                                   │
                                                   ▼
                                            highlights.mp4
```

---

## External Services

| Service | Used During | Purpose |
|---------|------------|---------|
| **AssemblyAI** | Ingest (step 2) | Transcription + speaker diarization |
| **API-Football** | Ingest (steps 4, 6) | Team/fixture search, match events |
| **OpenAI** | Query (step 3) | NLP query interpretation + label generation |
| **FFmpeg** | Query (step 5) | Video cutting, concatenation, fades |
| **Azure Blob** | Both (if cloud) | Storage backend for all artifacts |
