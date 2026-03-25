#!/usr/bin/env python3
"""Upload a local match video + metadata to Azure Blob for a catalog match_id.

Run on your machine (not in the API container). Optionally install yt-dlp
(see requirements-tools.txt) to download from YouTube first.

Examples:
  python scripts/upload_catalog_match.py \\
    --match-id istanbul-2005 \\
    --video ~/Videos/liverpool-milan-2005.mp4

  python scripts/upload_catalog_match.py \\
    --match-id istanbul-2005 \\
    --youtube-url 'https://www.youtube.com/watch?v=VIDEO_ID'

Requires AZURE_STORAGE_CONNECTION_STRING in the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from catalog.loader import get_match  # noqa: E402


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
    out = subprocess.check_output(cmd, text=True).strip()
    return float(out)


def _download_youtube(url: str, dest_dir: Path) -> Path:
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
    raise RuntimeError("yt-dlp finished but output video not found in temp dir")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match-id", required=True, help="Catalog match_id slug")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--video", type=Path, help="Local .mp4 file")
    g.add_argument(
        "--youtube-url",
        help="Download with yt-dlp (pip install -r requirements-tools.txt)",
    )
    args = parser.parse_args()

    entry = get_match(args.match_id)
    if entry is None:
        print(f"Unknown match_id: {args.match_id!r}", file=sys.stderr)
        sys.exit(1)

    conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    if not conn:
        print("Set AZURE_STORAGE_CONNECTION_STRING", file=sys.stderr)
        sys.exit(1)

    from azure.storage.blob import BlobServiceClient  # type: ignore[import-untyped]

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        if args.video:
            src = args.video.expanduser().resolve()
            if not src.is_file():
                print(f"Not a file: {src}", file=sys.stderr)
                sys.exit(1)
            if src.suffix.lower() != ".mp4":
                print("Local --video must be an .mp4 file (or use --youtube-url).", file=sys.stderr)
                sys.exit(1)
            video_path = work / "match.mp4"
            shutil.copy2(src, video_path)
        else:
            raw = _download_youtube(args.youtube_url, work)
            video_path = work / "match.mp4"
            if raw.resolve() != video_path.resolve():
                shutil.copy2(raw, video_path)

        duration = _ffprobe_duration(video_path)
        dest_name = "match.mp4"

        metadata = {
            "video_id": entry.match_id,
            "source": f"catalog:{entry.match_id}",
            "video_filename": dest_name,
            "duration_seconds": duration,
            "events_snapshot": entry.events_snapshot,
            "fixture_id": entry.fixture_id,
            "home_team": entry.home_team,
            "away_team": entry.away_team,
            "competition": entry.competition,
            "season_label": entry.season_label,
        }

        client = BlobServiceClient.from_connection_string(conn)
        videos = client.get_container_client("videos")

        vid = entry.match_id
        blob_video = f"{vid}/{dest_name}"
        blob_meta = f"{vid}/metadata.json"

        print(f"Uploading {video_path} → videos/{blob_video} …")
        with open(video_path, "rb") as f:
            videos.upload_blob(blob_video, f, overwrite=True)

        videos.upload_blob(
            blob_meta,
            json.dumps(metadata, indent=2),
            overwrite=True,
        )
        print(f"Uploaded metadata → videos/{blob_meta}")
        print(f'Done. POST /api/v1/jobs with {{"match_id": {entry.match_id!r}, ...}}')


if __name__ == "__main__":
    main()
