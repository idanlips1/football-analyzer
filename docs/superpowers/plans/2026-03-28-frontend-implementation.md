# Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dark cinematic React frontend to the football-analyzer, served alongside the existing FastAPI backend.

**Architecture:** Single-page React app in `frontend/` that calls the existing FastAPI `/api/v1/*` endpoints. Vite proxies API calls in dev; FastAPI serves the built SPA in production. Backend enriches the `/matches` endpoint with catalog metadata.

**Tech Stack:** Vite, React 18, TypeScript, Tailwind CSS v4, FastAPI (existing)

**Spec:** `docs/superpowers/specs/2026-03-28-frontend-design.md`

---

## File Map

### Backend (modify)
- `api/routes/catalog.py` — enrich `/matches` response with catalog metadata, filtered by storage
- `api/app.py` — simplify auth skip logic, mount `frontend/dist/` static files, SPA catch-all

### Backend (test)
- `tests/test_api_catalog.py` — new test file for enriched matches endpoint

### Frontend (create)
- `frontend/package.json` — dependencies and scripts
- `frontend/index.html` — Vite entry HTML
- `frontend/vite.config.ts` — Vite config with API proxy
- `frontend/tsconfig.json` — TypeScript config
- `frontend/tsconfig.app.json` — App-specific TS config
- `frontend/tsconfig.node.json` — Node/Vite TS config
- `frontend/src/index.css` — Tailwind directives + dark theme globals
- `frontend/src/main.tsx` — React entry point
- `frontend/src/types.ts` — TypeScript interfaces (Match, Job, JobResult, JobCreateResponse)
- `frontend/src/api.ts` — Fetch wrappers for all API endpoints
- `frontend/src/App.tsx` — Root component with state management
- `frontend/src/components/Sidebar.tsx` — Match list with search filter
- `frontend/src/components/MatchCard.tsx` — Individual match card in sidebar
- `frontend/src/components/VideoPlayer.tsx` — Video player with 4 states (empty/loading/ready/error)
- `frontend/src/components/QueryInput.tsx` — Text input with suggestion chips
- `frontend/src/components/QueryStatus.tsx` — Result banner (query, clips, duration)

---

## Task 1: Enrich the `/matches` API endpoint

**Files:**
- Modify: `api/routes/catalog.py`
- Create: `tests/test_api_catalog.py`

- [ ] **Step 1: Write tests for enriched matches endpoint**

Create `tests/test_api_catalog.py`:

```python
"""Tests for GET /api/v1/matches — enriched catalog response."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

_CATALOG = [
    {
        "match_id": "liv-v-mad-ucl-2024",
        "title": "Liverpool vs Real Madrid",
        "home_team": "Liverpool",
        "away_team": "Real Madrid",
        "competition": "Champions League",
        "season_label": "2024",
    },
]


@pytest.fixture()
def mock_storage() -> MagicMock:
    storage = MagicMock()
    storage.list_games.return_value = ["liv-v-mad-ucl-2024"]
    return storage


@pytest.fixture()
def client(mock_storage: MagicMock) -> Iterator[TestClient]:
    with (
        patch("api.app.API_KEYS", new=set()),
        patch("api.dependencies._storage", mock_storage),
        patch("api.routes.catalog.list_matches", return_value=_CATALOG),
    ):
        from api.app import create_app

        yield TestClient(create_app())


def test_matches_returns_enriched_catalog_entries(client: TestClient) -> None:
    """Matches endpoint returns catalog metadata, not just IDs."""
    resp = client.get("/api/v1/matches")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["matches"]) == 1
    match = data["matches"][0]
    assert match["match_id"] == "liv-v-mad-ucl-2024"
    assert match["home_team"] == "Liverpool"
    assert match["competition"] == "Champions League"


def test_matches_filters_out_non_ingested(mock_storage: MagicMock) -> None:
    """Catalog entries without corresponding storage are excluded."""
    mock_storage.list_games.return_value = []  # nothing in storage
    with (
        patch("api.app.API_KEYS", new=set()),
        patch("api.dependencies._storage", mock_storage),
        patch("api.routes.catalog.list_matches", return_value=_CATALOG),
    ):
        from api.app import create_app

        client = TestClient(create_app())
        resp = client.get("/api/v1/matches")

    assert resp.status_code == 200
    assert resp.json()["matches"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_catalog.py -v`
Expected: FAIL — `list_matches` not imported in catalog route yet.

- [ ] **Step 3: Update the catalog route**

Modify `api/routes/catalog.py`:

```python
"""Matches API: list queryable games available in storage."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies import get_storage
from catalog.loader import list_matches
from utils.storage import StorageBackend

router = APIRouter()


@router.get("/matches")
async def get_matches(storage: StorageBackend = Depends(get_storage)) -> dict:  # noqa: B008
    """List matches available for processing (must exist in both catalog and storage)."""
    storage_ids = set(storage.list_games())
    enriched = [m for m in list_matches() if m["match_id"] in storage_ids]
    return {"matches": enriched}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_api_catalog.py -v`
Expected: PASS

Also: `pytest tests/ -v` to verify no regressions. The existing `test_api_jobs.py` has a `test_list_matches` test that will break because the route now uses `list_matches()` from the catalog instead of `storage.list_games()` directly. Fix by adding a `list_matches` patch to the `test_api_jobs.py` fixture or the specific test — ensure the mock catalog includes the match IDs used in that test's mock storage.

- [ ] **Step 5: Run linters**

Run: `ruff check api/routes/catalog.py tests/test_api_catalog.py && mypy api/routes/catalog.py tests/test_api_catalog.py`

- [ ] **Step 6: Commit**

```bash
git add api/routes/catalog.py tests/test_api_catalog.py
git commit -m "feat(api): enrich /matches endpoint with catalog metadata

Return home_team, away_team, competition, season_label from the catalog,
filtered to only include matches that exist in storage."
```

---

## Task 2: Update auth middleware and add static file serving

**Files:**
- Modify: `api/app.py`

- [ ] **Step 1: Update auth middleware to skip non-API paths**

In `api/app.py`, change the auth middleware path check from:

```python
if request.url.path == "/api/v1/health" or request.url.path.startswith("/watch/"):
```

to:

```python
if not request.url.path.startswith("/api/") or request.url.path == "/api/v1/health":
```

This skips auth for `/`, `/assets/*`, `/watch/*`, and keeps the existing health endpoint exemption. All other `/api/v1/*` routes remain protected.

- [ ] **Step 2: Add static file mount for production**

Add to `api/app.py` inside `create_app()`, **after** the router includes:

```python
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Serve built frontend if it exists (production).
# IMPORTANT: This must come AFTER all router includes and explicit routes
# (/api/*, /watch/*) so the catch-all doesn't shadow them.
_frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/assets", StaticFiles(directory=_frontend_dist / "assets"), name="static")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        return FileResponse(_frontend_dist / "index.html")
```

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `pytest tests/test_api_health.py tests/test_api_watch.py tests/test_api_jobs.py -v`
Expected: All PASS. Health endpoint still skips auth (explicit exemption), watch page still works, job endpoints still require key when `API_KEYS` is set.

- [ ] **Step 4: Run linters**

Run: `ruff check api/app.py && mypy api/app.py`

- [ ] **Step 5: Commit**

```bash
git add api/app.py
git commit -m "feat(api): simplify auth skip logic, serve frontend static files

Skip auth for non-/api paths (SPA assets, /watch). Mount frontend/dist/
as static files with SPA catch-all when available."
```

---

## Task 3: Scaffold the React frontend

**Files:**
- Create: `frontend/package.json`, `frontend/index.html`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/tsconfig.app.json`, `frontend/tsconfig.node.json`, `frontend/postcss.config.js`, `frontend/src/index.css`, `frontend/src/main.tsx`, `frontend/src/vite-env.d.ts`

- [ ] **Step 1: Initialize the Vite + React + TypeScript project**

```bash
cd "/Users/idanlipschitz/Projects/System Development using AI/football-analyzer"
npm create vite@latest frontend -- --template react-ts
```

- [ ] **Step 2: Create .gitignore BEFORE installing dependencies**

Create `frontend/.gitignore` with `node_modules/` and `dist/` (the root `.gitignore` also covers `dist/` but being explicit is safer):

```
node_modules/
dist/
```

- [ ] **Step 3: Install Tailwind CSS v4**

```bash
cd frontend
npm install
npm install tailwindcss @tailwindcss/vite
```

- [ ] **Step 4: Configure Vite with Tailwind plugin and API proxy**

Replace `frontend/vite.config.ts`:

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/watch": "http://localhost:8000",
    },
  },
});
```

- [ ] **Step 5: Set up Tailwind CSS with dark theme**

Replace `frontend/src/index.css`:

```css
@import "tailwindcss";

@theme {
  --color-bg: #0a0a0a;
  --color-bg-card: #111111;
  --color-surface: rgba(255, 255, 255, 0.02);
  --color-surface-input: rgba(255, 255, 255, 0.04);
  --color-border-subtle: rgba(255, 255, 255, 0.06);
  --color-border-input: rgba(255, 255, 255, 0.10);
  --color-text-primary: #e0e0e0;
  --color-text-secondary: #888888;
  --color-text-muted: #555555;
  --color-accent: #e50914;
  --color-accent-surface: rgba(229, 9, 20, 0.12);
  --color-accent-border: rgba(229, 9, 20, 0.25);
}

body {
  margin: 0;
  background-color: var(--color-bg);
  color: var(--color-text-primary);
  font-family: Inter, system-ui, -apple-system, sans-serif;
  -webkit-font-smoothing: antialiased;
}
```

- [ ] **Step 6: Clean up scaffolded files**

Remove the default Vite boilerplate: `frontend/src/App.css`, `frontend/public/vite.svg`, `frontend/src/assets/react.svg`. Replace `frontend/src/App.tsx` with a placeholder:

```tsx
export default function App() {
  return (
    <div className="flex h-screen bg-bg">
      <p className="m-auto text-text-secondary">Loading...</p>
    </div>
  );
}
```

Update `frontend/src/main.tsx` to remove StrictMode wrapping issues:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
```

- [ ] **Step 7: Update index.html title**

In `frontend/index.html`, change the `<title>` to `Football Highlights`.

- [ ] **Step 8: Verify dev server starts**

```bash
cd frontend && npm run dev
```

Expected: Vite starts on `:5173`, browser shows dark page with "Loading..." text.

- [ ] **Step 9: Verify production build**

```bash
cd frontend && npm run build
```

Expected: `dist/` directory created with `index.html` and `assets/`.

- [ ] **Step 10: Commit**

```bash
git add frontend/
git commit -m "feat: scaffold React frontend with Vite, TypeScript, Tailwind

Dark theme configured with design tokens. Vite proxy set up for
API calls to FastAPI backend on :8000."
```

---

## Task 4: Types and API client

**Files:**
- Create: `frontend/src/types.ts`, `frontend/src/api.ts`

- [ ] **Step 1: Create TypeScript interfaces**

Create `frontend/src/types.ts`:

```typescript
export interface Match {
  match_id: string;
  title: string;
  home_team: string;
  away_team: string;
  competition: string;
  season_label: string;
}

export interface JobResult {
  download_url: string;
  duration_seconds: number;
  clip_count: number;
  expires_at: string;
}

export interface Job {
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

export interface JobCreateResponse {
  job_id: string;
  status: string;
  poll_url: string;
}
```

- [ ] **Step 2: Create API client**

Create `frontend/src/api.ts`:

```typescript
import type { Match, Job, JobCreateResponse } from "./types";

const BASE = "/api/v1";

export async function fetchMatches(): Promise<Match[]> {
  const res = await fetch(`${BASE}/matches`);
  if (!res.ok) throw new Error(`Failed to fetch matches: ${res.status}`);
  const data = await res.json();
  return data.matches;
}

export async function createJob(
  matchId: string,
  highlightsQuery: string
): Promise<JobCreateResponse> {
  const res = await fetch(`${BASE}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      match_id: matchId,
      highlights_query: highlightsQuery,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.error?.message ?? `Job creation failed: ${res.status}`);
  }
  return res.json();
}

export async function fetchJob(jobId: string): Promise<Job> {
  const res = await fetch(`${BASE}/jobs/${jobId}`);
  if (!res.ok) throw new Error(`Failed to fetch job: ${res.status}`);
  return res.json();
}
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd frontend && npx tsc --noEmit
```

Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts
git commit -m "feat: add TypeScript interfaces and API client

Types mirror the FastAPI response schemas. API client wraps
fetch for /matches, /jobs endpoints."
```

---

## Task 5: Sidebar and MatchCard components

**Files:**
- Create: `frontend/src/components/MatchCard.tsx`, `frontend/src/components/Sidebar.tsx`

- [ ] **Step 1: Create MatchCard component**

Create `frontend/src/components/MatchCard.tsx`:

```tsx
import type { Match } from "../types";

interface Props {
  match: Match;
  selected: boolean;
  onClick: () => void;
}

export default function MatchCard({ match, selected, onClick }: Props) {
  const displayName =
    match.home_team && match.away_team
      ? `${match.home_team} vs ${match.away_team}`
      : match.title;

  return (
    <button
      onClick={onClick}
      className={`w-full text-left rounded-lg p-3 transition-colors ${
        selected
          ? "bg-accent-surface border border-accent-border"
          : "bg-surface border border-border-subtle hover:border-border-input"
      }`}
    >
      <div
        className={`text-sm font-semibold ${selected ? "text-text-primary" : "text-text-secondary"}`}
      >
        {displayName}
      </div>
      <div className="flex gap-1.5 mt-1">
        {match.competition && (
          <span
            className={`text-xs px-1.5 rounded ${
              selected
                ? "bg-accent-surface text-accent"
                : "bg-surface-input text-text-muted"
            }`}
          >
            {match.competition}
          </span>
        )}
        <span className="text-xs text-text-muted">{match.season_label}</span>
      </div>
    </button>
  );
}
```

- [ ] **Step 2: Create Sidebar component**

Create `frontend/src/components/Sidebar.tsx`:

```tsx
import { useState } from "react";
import type { Match } from "../types";
import MatchCard from "./MatchCard";

interface Props {
  matches: Match[];
  selectedMatch: Match | null;
  onSelectMatch: (match: Match) => void;
  loading: boolean;
}

export default function Sidebar({
  matches,
  selectedMatch,
  onSelectMatch,
  loading,
}: Props) {
  const [filter, setFilter] = useState("");

  const filtered = matches.filter((m) => {
    const q = filter.toLowerCase();
    return (
      m.title.toLowerCase().includes(q) ||
      m.home_team.toLowerCase().includes(q) ||
      m.away_team.toLowerCase().includes(q) ||
      m.competition.toLowerCase().includes(q)
    );
  });

  return (
    <aside className="w-72 flex-shrink-0 bg-surface border-r border-border-subtle p-5 flex flex-col h-screen">
      {/* Header */}
      <div className="flex items-center gap-2.5 mb-6">
        <span className="text-2xl">⚽</span>
        <div>
          <div className="text-sm font-bold text-text-primary tracking-wide">
            MatchCut
          </div>
          <div className="text-[9px] text-text-muted uppercase tracking-widest">
            Football Highlights
          </div>
        </div>
      </div>

      {/* Search */}
      <div className="relative mb-4">
        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted text-sm">
          🔍
        </span>
        <input
          type="text"
          placeholder="Search matches..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-full bg-surface-input border border-border-input rounded-lg py-2 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent"
        />
      </div>

      {/* Match list */}
      <div className="text-[9px] text-text-muted uppercase tracking-[0.15em] font-semibold mb-2.5">
        Matches
      </div>
      <div className="flex flex-col gap-1.5 flex-1 overflow-y-auto">
        {loading ? (
          <div className="text-text-muted text-sm animate-pulse py-4 text-center">
            Loading matches...
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-text-muted text-sm py-4 text-center">
            {matches.length === 0 ? "No matches available" : "No matches found"}
          </div>
        ) : (
          filtered.map((m) => (
            <MatchCard
              key={m.match_id}
              match={m}
              selected={selectedMatch?.match_id === m.match_id}
              onClick={() => onSelectMatch(m)}
            />
          ))
        )}
      </div>
    </aside>
  );
}
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/MatchCard.tsx frontend/src/components/Sidebar.tsx
git commit -m "feat: add Sidebar and MatchCard components

Match list with search filter, competition badges, selected state
highlighting with red accent."
```

---

## Task 6: VideoPlayer component

**Files:**
- Create: `frontend/src/components/VideoPlayer.tsx`

- [ ] **Step 1: Create VideoPlayer with four states**

Create `frontend/src/components/VideoPlayer.tsx`:

```tsx
import type { Job } from "../types";

const PROGRESS_LABELS: Record<string, string> = {
  starting: "Starting...",
  loading_events: "Loading match events...",
  interpreting_query: "Understanding your request...",
  filtering: "Finding matching moments...",
  building_clips: "Building highlights...",
};

interface Props {
  job: Job | null;
  isLoading: boolean;
  onRetry: () => void;
}

export default function VideoPlayer({ job, isLoading, onRetry }: Props) {
  // State 1: Empty
  if (!job && !isLoading) {
    return (
      <div className="bg-bg-card rounded-xl aspect-video flex items-center justify-center">
        <div className="text-center">
          <div className="text-4xl text-text-muted mb-3">⚽</div>
          <p className="text-text-muted text-sm">
            Select a match and ask for highlights
          </p>
        </div>
      </div>
    );
  }

  // State 2: Loading
  if (isLoading || (job && (job.status === "queued" || job.status === "processing"))) {
    const stage = job?.progress ?? "starting";
    const label = PROGRESS_LABELS[stage] ?? stage;
    return (
      <div className="bg-bg-card rounded-xl aspect-video flex flex-col items-center justify-center">
        <div className="w-9 h-9 border-3 border-accent/20 border-t-accent rounded-full animate-spin mb-4" />
        <p className="text-text-primary text-sm font-semibold">
          Generating highlights...
        </p>
        <p className="text-text-muted text-xs mt-1">{label}</p>
      </div>
    );
  }

  // State 4: Error
  if (job?.status === "failed") {
    return (
      <div className="bg-bg-card rounded-xl aspect-video flex flex-col items-center justify-center">
        <div className="text-3xl mb-3">⚠️</div>
        <p className="text-text-primary text-sm font-semibold mb-1">
          Something went wrong
        </p>
        <p className="text-text-muted text-xs mb-4 max-w-md text-center px-4">
          {job.error ?? "An unknown error occurred"}
        </p>
        <button
          onClick={onRetry}
          className="bg-accent hover:bg-accent/80 text-white px-4 py-2 rounded-lg text-sm font-semibold transition-colors"
        >
          Try again
        </button>
      </div>
    );
  }

  // State 3: Ready
  if (job?.status === "completed" && job.result) {
    return (
      <div className="bg-bg-card rounded-xl overflow-hidden">
        <video
          key={job.result.download_url}
          src={job.result.download_url}
          controls
          preload="metadata"
          className="w-full aspect-video"
        />
      </div>
    );
  }

  return null;
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/VideoPlayer.tsx
git commit -m "feat: add VideoPlayer component with 4 states

Empty, loading (with progress labels), ready (video), and error
(with retry button)."
```

---

## Task 7: QueryInput and QueryStatus components

**Files:**
- Create: `frontend/src/components/QueryInput.tsx`, `frontend/src/components/QueryStatus.tsx`

- [ ] **Step 1: Create QueryInput component**

Create `frontend/src/components/QueryInput.tsx`:

```tsx
import { useState } from "react";

const SUGGESTIONS = [
  "Full summary",
  "Just goals",
  "Cards & VAR",
  "Second half",
];

interface Props {
  disabled: boolean;
  onSubmit: (query: string) => void;
}

export default function QueryInput({ disabled, onSubmit }: Props) {
  const [query, setQuery] = useState("");

  function handleSubmit() {
    const trimmed = query.trim();
    if (!trimmed) return;
    onSubmit(trimmed);
    setQuery("");
  }

  function handleChip(text: string) {
    onSubmit(text);
    setQuery("");
  }

  return (
    <div>
      <div
        className={`bg-surface-input border border-border-input rounded-xl px-4 py-3 flex items-center gap-3 ${
          disabled ? "opacity-50" : ""
        }`}
      >
        <span className="text-accent text-sm">✨</span>
        <input
          type="text"
          placeholder='Ask for highlights... "goals and penalties", "Salah moments"'
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
          disabled={disabled}
          className="flex-1 bg-transparent text-sm text-text-primary placeholder:text-text-muted focus:outline-none"
        />
        <button
          onClick={handleSubmit}
          disabled={disabled || !query.trim()}
          className="bg-accent hover:bg-accent/80 disabled:opacity-40 text-white px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-colors"
        >
          Generate
        </button>
      </div>
      <div className="flex gap-1.5 mt-2 flex-wrap">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            onClick={() => handleChip(s)}
            disabled={disabled}
            className="bg-surface-input border border-border-input text-text-secondary px-2.5 py-1 rounded-full text-[10px] hover:border-border-input hover:text-text-primary disabled:opacity-40 transition-colors"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Create QueryStatus component**

Create `frontend/src/components/QueryStatus.tsx`:

```tsx
import type { Job } from "../types";

interface Props {
  job: Job;
  onDismiss: () => void;
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s}s`;
}

export default function QueryStatus({ job, onDismiss }: Props) {
  if (job.status !== "completed" || !job.result) return null;

  return (
    <div className="bg-accent-surface border border-accent-border rounded-lg px-3.5 py-2.5 flex items-center justify-between">
      <div className="flex items-center gap-2">
        <span className="text-accent text-xs">✓</span>
        <span className="text-text-primary text-xs">
          &ldquo;{job.highlights_query}&rdquo;
        </span>
        <span className="text-text-muted text-[11px]">
          · {job.result.clip_count} clips ·{" "}
          {formatDuration(job.result.duration_seconds)}
        </span>
      </div>
      <button
        onClick={onDismiss}
        className="text-text-muted hover:text-text-secondary text-xs transition-colors"
      >
        ✕
      </button>
    </div>
  );
}
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/QueryInput.tsx frontend/src/components/QueryStatus.tsx
git commit -m "feat: add QueryInput and QueryStatus components

Text input with suggestion chips and result banner showing
query, clip count, and duration."
```

---

## Task 8: App root component — wire everything together

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Implement App with full state management**

Replace `frontend/src/App.tsx`:

```tsx
import { useCallback, useEffect, useRef, useState } from "react";
import type { Job, Match } from "./types";
import { createJob, fetchJob, fetchMatches } from "./api";
import Sidebar from "./components/Sidebar";
import VideoPlayer from "./components/VideoPlayer";
import QueryInput from "./components/QueryInput";
import QueryStatus from "./components/QueryStatus";

const POLL_INTERVAL_MS = 3000;

export default function App() {
  const [matches, setMatches] = useState<Match[]>([]);
  const [matchesLoading, setMatchesLoading] = useState(true);
  const [selectedMatch, setSelectedMatch] = useState<Match | null>(null);
  const [currentJob, setCurrentJob] = useState<Job | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Load matches on mount
  useEffect(() => {
    fetchMatches()
      .then(setMatches)
      .catch((err) => console.error("Failed to load matches:", err))
      .finally(() => setMatchesLoading(false));
  }, []);

  // Cleanup poll on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const cancelPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(
    (jobId: string) => {
      cancelPoll();
      pollRef.current = setInterval(async () => {
        try {
          const job = await fetchJob(jobId);
          setCurrentJob(job);
          if (job.status === "completed" || job.status === "failed") {
            cancelPoll();
            setIsLoading(false);
          }
        } catch (err) {
          console.error("Poll error:", err);
        }
      }, POLL_INTERVAL_MS);
    },
    [cancelPoll]
  );

  function handleSelectMatch(match: Match) {
    cancelPoll();
    setSelectedMatch(match);
    setCurrentJob(null);
    setIsLoading(false);
  }

  async function handleSubmitQuery(query: string) {
    if (!selectedMatch) return;
    cancelPoll();
    setIsLoading(true);
    setCurrentJob(null);

    try {
      const res = await createJob(selectedMatch.match_id, query);

      if (res.status === "completed") {
        // Cache hit — fetch full job for download_url
        const job = await fetchJob(res.job_id);
        setCurrentJob(job);
        setIsLoading(false);
      } else {
        // Queued — start polling
        setCurrentJob({
          job_id: res.job_id,
          status: "queued",
          progress: null,
          match_id: selectedMatch.match_id,
          highlights_query: query,
          query: `${selectedMatch.match_id} — ${query}`,
          result: null,
          error: null,
          created_at: new Date().toISOString(),
        });
        startPolling(res.job_id);
      }
    } catch (err) {
      setCurrentJob({
        job_id: "",
        status: "failed",
        progress: null,
        match_id: selectedMatch.match_id,
        highlights_query: query,
        query: "",
        result: null,
        error: err instanceof Error ? err.message : "An unknown error occurred",
        created_at: new Date().toISOString(),
      });
      setIsLoading(false);
    }
  }

  function handleRetry() {
    setCurrentJob(null);
    setIsLoading(false);
  }

  const displayName = selectedMatch
    ? selectedMatch.home_team && selectedMatch.away_team
      ? `${selectedMatch.home_team} vs ${selectedMatch.away_team}`
      : selectedMatch.title
    : "";

  return (
    <div className="flex h-screen bg-bg">
      <Sidebar
        matches={matches}
        selectedMatch={selectedMatch}
        onSelectMatch={handleSelectMatch}
        loading={matchesLoading}
      />

      <main className="flex-1 p-6 flex flex-col overflow-hidden">
        {selectedMatch ? (
          <>
            {/* Match header */}
            <div className="flex items-center justify-between mb-5">
              <div>
                <h1 className="text-xl font-bold text-text-primary">
                  {displayName}
                </h1>
                <p className="text-text-muted text-xs mt-0.5">
                  {[selectedMatch.competition, selectedMatch.season_label]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </div>
              {currentJob?.status === "completed" && currentJob.result && (
                <a
                  href={currentJob.result.download_url}
                  download
                  className="bg-surface-input border border-border-input text-text-secondary px-3.5 py-1.5 rounded-md text-xs hover:text-text-primary transition-colors"
                >
                  ⬇ Download
                </a>
              )}
            </div>

            {/* Video player */}
            <div className="mb-4">
              <VideoPlayer
                job={currentJob}
                isLoading={isLoading}
                onRetry={handleRetry}
              />
            </div>

            {/* Query status banner */}
            {currentJob?.status === "completed" && currentJob.result && (
              <div className="mb-4">
                <QueryStatus
                  job={currentJob}
                  onDismiss={() => setCurrentJob(null)}
                />
              </div>
            )}

            {/* Query input — pushed to bottom */}
            <div className="mt-auto">
              <QueryInput
                disabled={isLoading || !selectedMatch}
                onSubmit={handleSubmitQuery}
              />
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center">
              <div className="text-5xl mb-4">⚽</div>
              <h2 className="text-lg font-semibold text-text-primary mb-1">
                Football Highlights
              </h2>
              <p className="text-text-muted text-sm">
                Pick a match from the sidebar to get started
              </p>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 3: Verify dev server and UI renders**

```bash
cd frontend && npm run dev
```

Open http://localhost:5173 — verify:
- Dark background renders
- Sidebar shows with "Loading matches..." then either matches or "No matches available"
- If backend is running on :8000, matches should appear in sidebar

- [ ] **Step 4: Verify production build**

```bash
cd frontend && npm run build
```

Expected: No errors, `dist/` directory created.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat: wire up App root with full state management

Sidebar match selection, job submission with polling, cache hit
handling, error states, and poll cleanup on match switch."
```

---

## Task 9: End-to-end verification

- [ ] **Step 1: Run backend tests**

```bash
pytest tests/ -v
```

Expected: All tests pass. If any existing tests fail due to the enriched `/matches` response, fix the mocks.

- [ ] **Step 2: Run linters on backend changes**

```bash
ruff check . && mypy .
```

- [ ] **Step 3: Run frontend build**

```bash
cd frontend && npm run build
```

Expected: Clean build, no TypeScript or bundling errors.

- [ ] **Step 4: Test full integration (manual)**

Terminal 1:
```bash
uvicorn api.app:app --reload --port 8000
```

Terminal 2 (if using worker):
```bash
python -m worker
```

Terminal 3:
```bash
cd frontend && npm run dev
```

Open http://localhost:5173 and verify:
1. Sidebar loads matches from the catalog
2. Clicking a match shows its name and metadata
3. Typing a query and clicking Generate submits to the API
4. Loading spinner shows with progress stages
5. When complete, video plays in the player
6. Query status banner shows clip count and duration
7. Download button works
8. Switching matches clears the current job
9. Search filter works in sidebar

- [ ] **Step 5: Test production serving**

```bash
cd frontend && npm run build
cd .. && uvicorn api.app:app --port 8000
```

Open http://localhost:8000 — the React app should be served by FastAPI.

- [ ] **Step 6: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "fix: address integration issues from e2e testing"
```
