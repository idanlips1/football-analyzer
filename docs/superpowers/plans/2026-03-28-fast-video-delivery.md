# Fast Video Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce highlights file size ~3-4x via quality settings, and add a streaming `/watch/{job_id}` HTML endpoint so users can watch instantly instead of downloading.

**Architecture:** Two independent changes — (1) tweak FFmpeg encode settings in `config/settings.py` + `utils/ffmpeg.py`, (2) add an HTML viewer endpoint to the FastAPI app and update `main.py` to print its URL. Azure Blob Storage already serves HTTP range requests so the browser streams the video natively.

**Tech Stack:** Python 3.12, FastAPI, FFmpeg (libx264), Azure Blob Storage (SAS URLs)

---

## Files Changed

| File | Change |
|---|---|
| `config/settings.py` | Add `CLIP_SCALE = "1280:720"`, update `CLIP_CRF = 26`, `CLIP_AUDIO_BITRATE = "128k"` |
| `utils/ffmpeg.py` | Apply `CLIP_SCALE` scale filter in `apply_segment_fades` and `cut_clip` re-encode path |
| `api/app.py` | Add `GET /watch/{job_id}` HTML endpoint; skip API-key auth for `/watch/` paths |
| `main.py` | Print `Watch here:` URL alongside download URL when job completes |
| `tests/test_ffmpeg.py` | Tests: scale filter present when `CLIP_SCALE` set, absent when empty |
| `tests/test_api_watch.py` | Tests: 404 for unknown job, 404 for incomplete job, 200 HTML with video tag for completed job |

---

## Task 1: Lower output quality settings

**Files:**
- Modify: `config/settings.py`

- [ ] **Step 1: Update the three quality settings**

In `config/settings.py`, replace:
```python
# Encoding quality for re-encoded clips (lower = better quality, 18 ≈ visually lossless)
CLIP_CRF: int = 18

# Audio bitrate for re-encoded clips
CLIP_AUDIO_BITRATE: str = "192k"
```
With:
```python
# Encoding quality for re-encoded clips (lower = better quality, 18 ≈ visually lossless)
CLIP_CRF: int = 26

# Audio bitrate for re-encoded clips
CLIP_AUDIO_BITRATE: str = "128k"

# Output resolution for re-encoded clips — "WxH" string, e.g. "1280:720". Empty string disables scaling.
CLIP_SCALE: str = "1280:720"
```

- [ ] **Step 2: Commit**

```bash
git add config/settings.py
git commit -m "config: lower output quality (CRF 26, 720p, 128k audio)"
```

---

## Task 2: Apply scale filter in FFmpeg re-encode paths

**Files:**
- Modify: `utils/ffmpeg.py`
- Test: `tests/test_ffmpeg.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ffmpeg.py`:

```python
class TestScaleFilter:
    """Scale filter is applied in re-encode paths when CLIP_SCALE is set."""

    def test_apply_segment_fades_includes_scale_when_set(self, tmp_path: Path) -> None:
        src = tmp_path / "src.mp4"
        src.write_bytes(b"video")
        out = tmp_path / "out.mp4"

        with (
            patch("utils.ffmpeg.subprocess.run", side_effect=_fake_run_success) as mock,
            patch("config.settings.CLIP_SCALE", "1280:720"),
        ):
            apply_segment_fades(src, out, [10.0], fade_seconds=0.5)

        cmd = mock.call_args[0][0]
        vf = cmd[cmd.index("-vf") + 1]
        assert "scale=1280:720" in vf

    def test_apply_segment_fades_omits_scale_when_empty(self, tmp_path: Path) -> None:
        src = tmp_path / "src.mp4"
        src.write_bytes(b"video")
        out = tmp_path / "out.mp4"

        with (
            patch("utils.ffmpeg.subprocess.run", side_effect=_fake_run_success) as mock,
            patch("config.settings.CLIP_SCALE", ""),
        ):
            apply_segment_fades(src, out, [10.0], fade_seconds=0.5)

        cmd = mock.call_args[0][0]
        vf = cmd[cmd.index("-vf") + 1]
        assert "scale=" not in vf

    def test_cut_clip_reencode_includes_scale_when_set(self, tmp_path: Path) -> None:
        src = tmp_path / "src.mp4"
        src.write_bytes(b"video")
        out = tmp_path / "out.mp4"

        with (
            patch("utils.ffmpeg.subprocess.run", side_effect=_fake_run_success) as mock,
            patch("config.settings.CLIP_SCALE", "1280:720"),
        ):
            cut_clip(src, 10.0, 20.0, out, fade_duration=0.5)

        cmd = mock.call_args[0][0]
        vf = cmd[cmd.index("-vf") + 1]
        assert "scale=1280:720" in vf

    def test_cut_clip_stream_copy_never_has_scale(self, tmp_path: Path) -> None:
        src = tmp_path / "src.mp4"
        src.write_bytes(b"video")
        out = tmp_path / "out.mp4"

        with (
            patch("utils.ffmpeg.subprocess.run", side_effect=_fake_run_success) as mock,
            patch("config.settings.CLIP_SCALE", "1280:720"),
        ):
            cut_clip(src, 10.0, 20.0, out, fade_duration=0.0)

        cmd = mock.call_args[0][0]
        assert "scale=" not in " ".join(cmd)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_ffmpeg.py::TestScaleFilter -v
```
Expected: 4 FAILures — `CLIP_SCALE` doesn't exist yet in `utils/ffmpeg.py`.

- [ ] **Step 3: Import CLIP_SCALE in `utils/ffmpeg.py` and apply it**

At the top of `apply_segment_fades`, add the import and append scale to `vf_parts`:

In `apply_segment_fades`, replace:
```python
    from config.settings import CLIP_AUDIO_BITRATE, CLIP_CRF
```
With:
```python
    from config.settings import CLIP_AUDIO_BITRATE, CLIP_CRF, CLIP_SCALE
```

Then, right before the `cmd = [...]` block in `apply_segment_fades`, add:
```python
    if CLIP_SCALE:
        vf_parts.append(f"scale={CLIP_SCALE}")
```

In `cut_clip`, the re-encode path already does `from config.settings import CLIP_AUDIO_BITRATE, CLIP_CRF`. Replace that line with:
```python
    from config.settings import CLIP_AUDIO_BITRATE, CLIP_CRF, CLIP_SCALE
```

Then in the re-encode path of `cut_clip`, replace the `-vf` value build:
```python
            "-vf",
            f"fade=t=in:st=0:d={fade:.3f},fade=t=out:st={fade_out_start:.3f}:d={fade:.3f}",
```
With:
```python
            "-vf",
            ",".join(filter(None, [
                f"fade=t=in:st=0:d={fade:.3f},fade=t=out:st={fade_out_start:.3f}:d={fade:.3f}",
                f"scale={CLIP_SCALE}" if CLIP_SCALE else "",
            ])),
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_ffmpeg.py -v
```
Expected: all PASS (including the 4 new tests).

- [ ] **Step 5: Commit**

```bash
git add utils/ffmpeg.py tests/test_ffmpeg.py
git commit -m "feat: apply CLIP_SCALE resolution filter in FFmpeg re-encode paths"
```

---

## Task 3: Add `/watch/{job_id}` streaming endpoint

**Files:**
- Modify: `api/app.py`
- Test: `tests/test_api_watch.py` *(new file)*

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_watch.py`:

```python
"""Tests for GET /watch/{job_id} HTML streaming endpoint."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from models.job import Job, JobResult, JobStatus
from utils.job_store import InMemoryJobStore


@pytest.fixture()
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture()
def client(store: InMemoryJobStore) -> Iterator[TestClient]:
    with patch("api.dependencies._store", store):
        from api.app import create_app

        app = create_app()
        yield TestClient(app)


def _completed_job(store: InMemoryJobStore) -> Job:
    job = Job(match_id="barcelona-2005", highlights_query="goals")
    job.status = JobStatus.COMPLETED
    job.result = JobResult(
        download_url="https://blob.example/highlights/vid.mp4?sig=abc",
        duration_seconds=120.0,
        clip_count=3,
        expires_at="2026-03-29T10:00:00+00:00",
    )
    store.create(job)
    return job


def test_watch_unknown_job_returns_404(client: TestClient) -> None:
    response = client.get("/watch/doesnotexist")
    assert response.status_code == 404


def test_watch_queued_job_returns_404(client: TestClient, store: InMemoryJobStore) -> None:
    job = Job(match_id="test", highlights_query="goals")
    store.create(job)
    response = client.get(f"/watch/{job.job_id}")
    assert response.status_code == 404


def test_watch_completed_job_returns_html(client: TestClient, store: InMemoryJobStore) -> None:
    job = _completed_job(store)
    response = client.get(f"/watch/{job.job_id}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_watch_html_contains_video_tag_with_sas_url(
    client: TestClient, store: InMemoryJobStore
) -> None:
    job = _completed_job(store)
    response = client.get(f"/watch/{job.job_id}")
    body = response.text
    assert "<video" in body
    assert "https://blob.example/highlights/vid.mp4" in body


def test_watch_requires_no_api_key(client: TestClient, store: InMemoryJobStore) -> None:
    """Watch endpoint must be accessible without X-API-Key (opened in browser)."""
    job = _completed_job(store)
    with patch("api.app.API_KEYS", ["secret-key"]):
        from api.app import create_app
        app = create_app()
        with patch("api.dependencies._store", store):
            test_client = TestClient(app)
            response = test_client.get(f"/watch/{job.job_id}")
    assert response.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_api_watch.py -v
```
Expected: all FAIL — endpoint doesn't exist yet.

- [ ] **Step 3: Add the `/watch/{job_id}` endpoint to `api/app.py`**

Replace the full content of `api/app.py` with:

```python
"""FastAPI application factory."""

from __future__ import annotations

import html as html_module

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from api.dependencies import get_job_store
from api.routes import catalog, jobs
from config.settings import API_KEYS
from models.job import JobStatus


def create_app() -> FastAPI:
    app = FastAPI(title="Football Highlights API", version="1.0.0")

    @app.middleware("http")
    async def api_key_auth(request: Request, call_next):  # type: ignore[no-untyped-def]
        # Skip auth for health check and browser-facing watch page
        if request.url.path == "/api/v1/health" or request.url.path.startswith("/watch/"):
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        if API_KEYS and api_key not in API_KEYS:
            return JSONResponse(
                status_code=401,
                content={
                    "error": {"code": "unauthorized", "message": "Invalid or missing API key"}
                },
            )
        return await call_next(request)

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/watch/{job_id}", response_class=HTMLResponse, include_in_schema=False)
    async def watch_job(job_id: str) -> HTMLResponse:
        store = get_job_store()
        job = store.get(job_id)
        if job is None or job.status != JobStatus.COMPLETED or not job.result:
            return HTMLResponse("<h1>Not ready yet — check back soon.</h1>", status_code=404)

        sas_url = html_module.escape(job.result.download_url, quote=True)
        title = html_module.escape(job.match_id)
        body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title} — Highlights</title>
  <style>
    body {{ margin: 0; background: #000; display: flex; flex-direction: column;
           align-items: center; justify-content: center; min-height: 100vh; font-family: sans-serif; }}
    h1   {{ color: #fff; font-size: 1rem; margin-bottom: 1rem; opacity: 0.8; }}
    video {{ max-width: 960px; width: 100%; outline: none; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <video src="{sas_url}" controls autoplay></video>
</body>
</html>"""
        return HTMLResponse(body)

    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(catalog.router, prefix="/api/v1")

    return app


app = create_app()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_api_watch.py -v
```
Expected: all PASS.

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
pytest -v
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add api/app.py tests/test_api_watch.py
git commit -m "feat: add /watch/{job_id} HTML streaming endpoint"
```

---

## Task 4: Print watch URL in the CLI

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Update `_poll_job` to accept and use `job_id`**

In `main.py`, replace the `_poll_job` signature and the "Done!" print block:

```python
def _poll_job(poll_url: str, job_id: str) -> None:
    url = f"{API_BASE_URL}{poll_url}"
    print("\n  Job queued. Waiting for worker to process on Azure...")
    while True:
        try:
            with urllib.request.urlopen(url) as response:  # nosec B310
                job_data = json.loads(response.read().decode())
                status = job_data.get("status")

                if status == "completed":
                    result = job_data.get("result", {})
                    watch_url = f"{API_BASE_URL}/watch/{job_id}"
                    print(f"\n  Watch here:  {watch_url}")
                    print(f"  Download:    {result.get('download_url')}")
                    print(
                        f"  Duration: {result.get('duration_seconds', 0)}s | "
                        f"Clips: {result.get('clip_count', 0)}\n"
                    )
                    return
                elif status == "failed":
                    print(f"\n  Job failed: {job_data.get('error')}\n", file=sys.stderr)
                    return
                else:
                    stage = job_data.get("progress", "processing")
                    sys.stdout.write(f"\r  Status: {status} ({stage})".ljust(50))
                    sys.stdout.flush()
        except Exception as e:
            print(f"\n  Error polling job: {e}", file=sys.stderr)
            return
        time.sleep(3)
```

- [ ] **Step 2: Update the call site in `_game_repl` to pass `job_id`**

In `_game_repl`, replace:
```python
        if job_info:
            if "status" in job_info and job_info["status"] == "completed":
                print("\n  Job instantly found in cache!")
                result = job_info.get("result", {})
                print(f"  Done! Download here: {result.get('download_url')}\n")
            else:
                _poll_job(job_info["poll_url"])
```
With:
```python
        if job_info:
            job_id = job_info.get("job_id", "")
            if "status" in job_info and job_info["status"] == "completed":
                print("\n  Job instantly found in cache!")
                result = job_info.get("result", {})
                watch_url = f"{API_BASE_URL}/watch/{job_id}"
                print(f"  Watch here:  {watch_url}")
                print(f"  Download:    {result.get('download_url')}\n")
            else:
                _poll_job(job_info["poll_url"], job_id)
```

- [ ] **Step 3: Run the full test suite**

```bash
pytest -v
```
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: print watch URL in CLI alongside download link"
```
