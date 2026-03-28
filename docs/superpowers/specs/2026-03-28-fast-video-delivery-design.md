# Fast Video Delivery — Design Spec
**Date:** 2026-03-28
**Status:** Approved

## Problem

The highlights video (~580 MB) takes ~40 minutes to download from Azure Blob Storage because:
1. The output is encoded at near-lossless quality (CRF 18, 1080p, 192k audio).
2. The CLI gives the user a raw SAS download URL — the browser downloads the entire file before playback.

The Azure pod itself cannot be upgraded. The file is already in Azure Blob Storage before the link is served.

## Solution Overview

Two complementary changes:

1. **Lower output quality** — smaller file, faster to produce and download.
2. **Streaming frontend** — video starts playing in ~2 seconds by streaming directly from Azure Blob; no full download needed.

---

## Part 1 — Quality Reduction

### Settings (`config/settings.py`)

| Setting | Before | After |
|---|---|---|
| `CLIP_CRF` | 18 | 26 |
| `CLIP_AUDIO_BITRATE` | `"192k"` | `"128k"` |
| `CLIP_SCALE` *(new)* | — | `"1280:720"` |

`CLIP_SCALE` controls output resolution. Set to `""` to disable scaling (e.g. for local dev).

### FFmpeg (`utils/ffmpeg.py`)

- `apply_segment_fades`: add `-vf scale={CLIP_SCALE}` to the filter chain when `CLIP_SCALE` is non-empty.
- `cut_clip` (re-encode path only, i.e. `fade_duration > 0`): same scale filter.
- Stream-copy path (`-c copy`) is untouched — it only produces intermediate clips, not the final output.

### Expected outcome

~150–200 MB for a 4-minute highlights clip (vs. ~580 MB). Still visually good for a demo.

---

## Part 2 — Streaming Frontend

### New API endpoint

`GET /watch/{job_id}`

- Fetches job from job store (same source as the polling endpoint).
- **If completed:** returns a minimal HTML page (plain string, no template library) with:
  - `<video src="{sas_url}" controls autoplay style="width:100%">`
  - Black background, centered, match title in `<title>` and a heading.
- **If not found / not completed:** returns a plain-text "not ready yet" response (HTTP 404 / 202).

No new dependencies required.

### CLI change (`main.py`)

When a job completes, print:

```
Watch here:  http://<API_BASE_URL>/watch/<job_id>
Download:    <sas_url>
```

Both URLs are shown so the user can choose. The watch URL is listed first as the recommended path.

### How streaming works

Azure Blob Storage responds to HTTP `Range` requests (HTTP 206 Partial Content). The browser's native video player requests chunks on demand — identical to how streaming services work. The Azure pod's connection speed is irrelevant because the video is served directly from Blob Storage to the user's browser. Playback starts within ~2 seconds.

---

## Files Changed

| File | Change |
|---|---|
| `config/settings.py` | Add `CLIP_SCALE`, update `CLIP_CRF` and `CLIP_AUDIO_BITRATE` |
| `utils/ffmpeg.py` | Apply `CLIP_SCALE` in `apply_segment_fades` and `cut_clip` re-encode path |
| `api/` (router file) | Add `GET /watch/{job_id}` endpoint |
| `main.py` | Print watch URL alongside download URL on job completion |

## Out of Scope

- Ingestion quality (yt-dlp format) — existing catalog videos are already at 1080p; changing this only affects future ingestions.
- HLS/adaptive bitrate streaming — overkill for a coursework demo.
- Upgrading the Azure pod.
