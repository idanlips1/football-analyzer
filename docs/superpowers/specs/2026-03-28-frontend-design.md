# Frontend Design: Football Highlights App

## Problem

The football-analyzer pipeline generates highlights from full match videos using API-Football events, transcription-based alignment, and FFmpeg. The current interface is a CLI (`main.py`) that talks to the FastAPI backend. There is no visual frontend — the only browser experience is a bare `<video>` tag on `/watch/{job_id}`. For sharing with friends, we need an intuitive, visually appealing web app.

## Design Decisions

- **Audience**: Small group of friends — intuitive UX, no auth needed
- **Core flow**: Browse matches + request custom highlights equally
- **Data depth**: Minimal — match cards, video player, query input (no event timelines or dashboards)
- **Visual style**: Dark cinematic (Netflix/DAZN-inspired, `#0a0a0a` background, `#e50914` red accent)
- **Layout**: Single-page app with sidebar match list + main content area
- **Query results**: Inline replace — video player swaps to highlights, one video at a time
- **Repo structure**: `frontend/` directory inside the existing monorepo

## Tech Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Build tool | Vite | Fast HMR, zero-config TypeScript |
| Framework | React 18+ | Component model, ecosystem |
| Language | TypeScript | Type safety, matches backend conventions |
| Styling | Tailwind CSS | Utility-first, easy dark theming, no CSS files to manage |
| HTTP client | fetch (native) | Simple API, no extra dependency |

No routing library needed — single page, no URL navigation.

## App Structure

```
frontend/
├── index.html
├── package.json
├── tsconfig.json
├── tailwind.config.ts
├── vite.config.ts
├── postcss.config.js
├── src/
│   ├── main.tsx              # React entry point
│   ├── App.tsx               # Root component, state management
│   ├── api.ts                # API client (fetch wrappers for /api/v1/*)
│   ├── types.ts              # TypeScript interfaces matching API schemas
│   ├── components/
│   │   ├── Sidebar.tsx       # Match list with search filter
│   │   ├── MatchCard.tsx     # Individual match item in sidebar
│   │   ├── VideoPlayer.tsx   # HTML5 video wrapper with loading states
│   │   ├── QueryInput.tsx    # Text input + quick suggestion chips
│   │   └── QueryStatus.tsx   # Result banner (clips, duration) or processing spinner
│   └── index.css             # Tailwind directives + minimal global styles
```

## Component Design

### App (root)

State:
- `matches: Match[]` — loaded from API on mount
- `selectedMatch: Match | null` — currently selected match
- `currentJob: Job | null` — active or completed job
- `isLoading: boolean` — query in progress

### Sidebar

- Scrollable list of `MatchCard` components
- Text filter input at top (client-side filter on team names / competition)
- Selected match highlighted with red accent border
- Each card shows: home vs away, competition badge, season

### VideoPlayer

Four states:
1. **Empty** — no match selected. Shows placeholder with prompt text.
2. **Loading** — job queued/processing. Shows spinner with progress stage text mapped to friendly labels:
   - `"starting"` → "Starting..."
   - `"loading_events"` → "Loading match events..."
   - `"interpreting_query"` → "Understanding your request..."
   - `"filtering"` → "Finding matching moments..."
   - `"building_clips"` → "Building highlights..."
3. **Ready** — job completed. Renders `<video>` with `src={job.result.download_url}`, controls, `preload="metadata"`.
4. **Error** — job failed. Shows `job.error` message with a "Try again" button that re-enables the query input.

### QueryInput

- Text input with placeholder examples
- "Generate" submit button (red accent)
- Quick suggestion chips below: "Full summary", "Just goals", "Cards & VAR", "Second half"
- Chips act as one-click shortcuts that fill and submit the query
- Disabled while a job is loading

### QueryStatus

- Shown between video player and query input when a job has completed
- Displays: query text, clip count, duration
- Dismiss/clear button

## TypeScript Interfaces

```typescript
// types.ts — mirrors API response shapes

interface Match {
  match_id: string;
  title: string;
  home_team: string;
  away_team: string;
  competition: string;
  season_label: string;
}

interface JobResult {
  download_url: string;
  duration_seconds: number;
  clip_count: number;
  expires_at: string;
}

interface Job {
  job_id: string;
  status: "queued" | "processing" | "completed" | "failed";
  progress: string | null;
  match_id: string;
  highlights_query: string;
  query: string;
  result: JobResult | null;
  error: string | null;
  created_at: string;
}

interface JobCreateResponse {
  job_id: string;
  status: string;
  poll_url: string;
}
```

## API Integration

### Existing endpoints (no changes needed)

| Endpoint | Usage |
|----------|-------|
| `POST /api/v1/jobs` | Submit highlights query |
| `GET /api/v1/jobs/{job_id}` | Poll job status (3s interval) |

### Backend changes required

#### 1. Enrich `GET /api/v1/matches` response

**Current response:**
```json
{"matches": [{"match_id": "..."}]}
```

**Required response:**
```json
{
  "matches": [
    {
      "match_id": "man-utd-v-liverpool-fa-cup-2024",
      "title": "Manchester United vs Liverpool",
      "home_team": "Manchester United",
      "away_team": "Liverpool",
      "competition": "FA Cup",
      "season_label": "2023-24"
    }
  ]
}
```

**Change**: In `api/routes/catalog.py`, return the **intersection** of catalog entries and storage — only matches that exist in both `catalog.loader.list_matches()` AND `storage.list_games()`. This prevents showing matches that haven't been ingested yet.

```python
from catalog.loader import list_matches

@router.get("/matches")
async def get_matches(storage: StorageBackend = Depends(get_storage)) -> dict:
    storage_ids = set(storage.list_games())
    enriched = [m for m in list_matches() if m["match_id"] in storage_ids]
    return {"matches": enriched}
```

**Note**: Current catalog `matches.json` entries may have empty `home_team`/`away_team` fields. The frontend should fall back to displaying the `title` field when team names are blank.

**File**: `api/routes/catalog.py`

#### 2. Add Vite dev proxy (preferred over CORS)

**File**: `frontend/vite.config.ts`

Use Vite's built-in proxy to forward `/api/*` requests to the FastAPI backend during development. This avoids CORS entirely — in dev, all requests go through Vite's server; in production, both frontend and API are served from the same origin.

```typescript
export default defineConfig({
  server: {
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
```

No CORS middleware needed on the backend.

#### 3. Serve built frontend in production

**File**: `api/app.py`

Mount `frontend/dist/` as static files. Must be mounted **after** all API routes to avoid shadowing `/api/*` and `/watch/*` paths. Use a catch-all route for SPA fallback (return `index.html` for any non-API, non-static path). Only mount if `frontend/dist/` exists.

#### 4. Auth middleware: skip non-API paths

**File**: `api/app.py`

Simplify the auth skip logic: skip auth for any path that does **not** start with `/api/`. This covers `/`, `/assets/*` (Vite build output), `/watch/*`, and any future non-API routes. All `/api/v1/*` endpoints remain behind the key check (which already passes when `API_KEYS` is empty).

## Visual Design Tokens

```
Background:       #0a0a0a (main), #111111 (cards/player)
Surface:          rgba(255,255,255,0.02) (sidebar), rgba(255,255,255,0.04) (inputs)
Border:           rgba(255,255,255,0.06) (subtle), rgba(255,255,255,0.10) (inputs)
Text primary:     #e0e0e0
Text secondary:   #888888
Text muted:       #555555
Accent:           #e50914 (red — buttons, selected states, progress)
Accent surface:   rgba(229,9,20,0.12) (selected match bg)
Accent border:    rgba(229,9,20,0.25) (selected match border)
Border radius:    8px (cards), 12px (player, input), 14px (chips)
Font:             system font stack (Inter if available)
```

## Data Flow

```
1. App mounts → GET /api/v1/matches → populate sidebar
   (show skeleton/spinner in sidebar while loading; empty state if 0 matches)
2. User clicks match → set selectedMatch (no API call, just UI state)
3. User types query + clicks Generate (or clicks chip)
   → Cancel any in-flight polling interval (clear previous job poll)
   → POST /api/v1/jobs { match_id, highlights_query }
   → Response: { job_id, status, poll_url }
4. If status == "completed" (cache hit):
   → GET /api/v1/jobs/{job_id} to fetch full job with result.download_url
   → Show video immediately
5. If status == "queued":
   → Poll GET /api/v1/jobs/{job_id} every 3 seconds
   → Show spinner + progress stage
   → When completed → swap to video player
   → When failed → show error + "Try again" button
6. User types new query → cancel active poll → repeat from step 3
7. User clicks different match → cancel active poll → clear currentJob → step 2
```

## Development Workflow

```bash
# Terminal 1: FastAPI backend
uvicorn api.app:app --reload --port 8000

# Terminal 2: Vite dev server (proxies /api/* to :8000)
cd frontend && npm run dev
# Opens at http://localhost:5173 — all API calls proxied to backend
```

## Production Build

```bash
cd frontend && npm run build
# Output: frontend/dist/
# FastAPI serves this at / via StaticFiles mount
```

## Verification Plan

1. **Backend**: Run `pytest` — existing tests should pass unchanged. New catalog route returns enriched data.
2. **Frontend build**: `cd frontend && npm run build` succeeds with no TypeScript errors.
3. **Integration**: Start FastAPI + worker, open `http://localhost:8000`, sidebar loads matches from catalog, clicking a match shows its info, typing a query generates highlights, video plays back.
4. **Dev mode**: `npm run dev` on `:5173` proxies API calls to `:8000`, hot reload works.
