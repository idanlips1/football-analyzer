#!/usr/bin/env python3
"""Download each catalog match from YouTube (ytsearch + heuristics) and upload to Azure Blob.

Requires: pip install -r requirements-tools.txt, ffmpeg, az CLI (optional) for connection string.

Environment:
  AZURE_STORAGE_CONNECTION_STRING — or set AZURE_STORAGE_ACCOUNT + AZURE_RESOURCE_GROUP for ``az`` lookup.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Per-match search queries (tuned for long full-match style uploads)
SEARCHES: dict[str, str] = {
    "istanbul-2005": "Liverpool AC Milan 2005 UEFA Champions League final full match",
    "barcelona-psg-2017": "Barcelona PSG 6-1 2017 Champions League full match",
    "germany-brazil-2014": "Brazil Germany 7-1 2014 FIFA World Cup semi final full match",
    "liverpool-barcelona-2019": "Liverpool Barcelona 4-0 2019 Champions League semi final full match",
    "argentina-france-2022": "Argentina France 2022 FIFA World Cup final full match",
    "tottenham-ajax-2019": "Tottenham Ajax 2019 Champions League semi final second leg full match",
    "real-madrid-man-city-2022": "Real Madrid Manchester City 2022 Champions League semi final full match",
    "chelsea-bayern-2012": "Chelsea Bayern Munich 2012 Champions League final full match",
    "ajax-real-madrid-2019": "Ajax Real Madrid 4-1 2019 Champions League full match",
    "leicester-qpr-2014": "Leicester QPR 2015 Premier League final day full match",
}


def _connection_string() -> str:
    s = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    if s:
        return s
    account = os.environ.get("AZURE_STORAGE_ACCOUNT", "footballhlstorage").strip()
    rg = os.environ.get("AZURE_RESOURCE_GROUP", "football-hl-rg").strip()
    try:
        out = subprocess.check_output(
            [
                "az",
                "storage",
                "account",
                "show-connection-string",
                "--name",
                account,
                "--resource-group",
                rg,
                "--query",
                "connectionString",
                "-o",
                "tsv",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


_BAD_TITLE = re.compile(
    r"radio|bbc radio|podcast|audio only|commentary only|highlights?\s*$|best moments",
    re.I,
)
_GOOD_TITLE = re.compile(r"full\s*match|match\s*replay|full\s*game|extended\s*highlights", re.I)


def _pick_video_url(query: str) -> tuple[str, str, int]:
    import yt_dlp

    ydl_opts: dict = {
        "quiet": True,
        "noplaylist": True,
        "ignoreerrors": True,
        "socket_timeout": 30,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch12:{query}", download=False)
    entries = [e for e in (info.get("entries") or []) if e]
    scored: list[tuple[ float, str, str, int]] = []
    for e in entries:
        vid = e.get("id") or ""
        title = e.get("title") or ""
        dur = int(e.get("duration") or 0)
        if dur < 3200 or dur > 20000:
            continue
        if _BAD_TITLE.search(title):
            continue
        score = 0.0
        if _GOOD_TITLE.search(title):
            score += 5.0
        if "full match" in title.lower():
            score += 3.0
        # Prefer TV broadcast-ish length (~90–120 min + extra time)
        if 4000 <= dur <= 9000:
            score += 2.0
        if "highlight" in title.lower() and "full" not in title.lower():
            score -= 5.0
        scored.append((score, vid, title, dur))
    scored.sort(key=lambda x: (x[0], x[3]), reverse=True)
    if not scored:
        raise RuntimeError(f"No suitable video for query: {query!r}")
    _, vid, title, dur = scored[0]
    return f"https://www.youtube.com/watch?v={vid}", title, dur


def _download_video(url: str, dest_dir: Path) -> Path:
    import yt_dlp

    tmpl = str(dest_dir / "match.%(ext)s")
    ydl_opts: dict = {
        "outtmpl": tmpl,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "quiet": False,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    for name in ("match.mp4", "match.mkv", "match.webm"):
        p = dest_dir / name
        if p.exists():
            return p
    mp4s = list(dest_dir.glob("match*.mp4"))
    if mp4s:
        return mp4s[0]
    raise RuntimeError("Download finished but match*.mp4 not found")


def _ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    return float(subprocess.check_output(cmd, text=True).strip())


def _upload(match_id: str, video_path: Path, metadata: dict) -> None:
    from azure.storage.blob import BlobServiceClient  # type: ignore[import-untyped]

    conn = _connection_string()
    if not conn:
        raise RuntimeError("No AZURE_STORAGE_CONNECTION_STRING and az CLI lookup failed")

    client = BlobServiceClient.from_connection_string(conn)
    videos = client.get_container_client("videos")
    dest_name = "match.mp4"
    blob_video = f"{match_id}/{dest_name}"
    blob_meta = f"{match_id}/metadata.json"

    print(f"  Uploading → videos/{blob_video} …")
    with open(video_path, "rb") as f:
        videos.upload_blob(blob_video, f, overwrite=True)
    videos.upload_blob(
        blob_meta,
        json.dumps(metadata, indent=2),
        overwrite=True,
    )
    print(f"  metadata → videos/{blob_meta}")


def main() -> None:
    from catalog.loader import load_catalog

    conn = _connection_string()
    if not conn:
        print(
            "Set AZURE_STORAGE_CONNECTION_STRING or install Azure CLI and login "
            "(defaults: account footballhlstorage, RG football-hl-rg).",
            file=sys.stderr,
        )
        sys.exit(1)

    only = os.environ.get("SYNC_ONLY_MATCH_ID", "").strip()

    for entry in load_catalog():
        mid = entry.match_id
        if only and mid != only:
            continue
        query = SEARCHES.get(mid)
        if not query:
            print(f"SKIP {mid}: no search query", file=sys.stderr)
            continue

        print(f"\n=== {mid} ===\n  Query: {query}")
        try:
            url, title, dur = _pick_video_url(query)
        except Exception as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)
            continue
        print(f"  Picked ({dur}s): {title[:90]}")
        print(f"  URL: {url}")

        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            try:
                raw = _download_video(url, work)
            except Exception as exc:
                print(f"  DOWNLOAD FAILED: {exc}", file=sys.stderr)
                continue
            video_path = work / "match.mp4"
            if raw.resolve() != video_path.resolve():
                shutil.copy2(raw, video_path)

            duration = _ffprobe_duration(video_path)
            meta = {
                "video_id": mid,
                "source": f"catalog:{mid}",
                "video_filename": "match.mp4",
                "duration_seconds": duration,
                "events_snapshot": entry.events_snapshot,
                "fixture_id": entry.fixture_id,
                "home_team": entry.home_team,
                "away_team": entry.away_team,
                "competition": entry.competition,
                "season_label": entry.season_label,
                "youtube_source": url,
            }
            try:
                _upload(mid, video_path, meta)
            except Exception as exc:
                print(f"  UPLOAD FAILED: {exc}", file=sys.stderr)

    print("\nDone.")


if __name__ == "__main__":
    main()
