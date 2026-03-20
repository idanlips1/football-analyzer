"""Stage 1 (new) — Match finder: YouTube search, API-Football fixture lookup,
and video download with metadata persistence."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import yt_dlp

from config.settings import (
    API_FOOTBALL_BASE_URL,
    API_FOOTBALL_KEY,
    MIN_DURATION_SECONDS,
    PIPELINE_WORKSPACE,
)
from utils.ffmpeg import FFprobeError, get_video_duration
from utils.logger import get_logger

log = get_logger(__name__)

METADATA_FILENAME = "metadata.json"
_MIN_FULL_MATCH_SECONDS = 45 * 60


class MatchFinderError(Exception):
    """Raised when match finding or download fails."""


# ── Public API ──────────────────────────────────────────────────────────────


def is_url(text: str) -> bool:
    """Return True if *text* looks like an HTTP(S) URL."""
    return text.startswith("http://") or text.startswith("https://")


def search_youtube(
    query: str,
    max_results: int = 5,
) -> list[dict[str, Any]]:
    """Search YouTube for full-match videos matching *query*.

    Uses yt-dlp's ``ytsearch`` pseudo-URL.  Results shorter than 45 min are
    filtered out (likely highlights, not full matches).  Returned list is
    sorted by duration descending so the longest (most likely full match)
    comes first.
    """
    search_url = f"ytsearch{max_results}:{query} full match"
    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "skip_download": True,
    }

    log.info("Searching YouTube: %s", search_url)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_url, download=False)
    except Exception:
        log.exception("YouTube search failed")
        return []

    if not info or "entries" not in info:
        return []

    entries: list[dict[str, Any]] = info["entries"]  # type: ignore[assignment]
    results: list[dict[str, Any]] = []
    for entry in entries:
        if not entry:
            continue
        duration = entry.get("duration") or 0
        if duration < _MIN_FULL_MATCH_SECONDS:
            continue
        results.append(
            {
                "title": entry.get("title", ""),
                "url": entry.get("webpage_url", ""),
                "duration_seconds": duration,
                "video_id": entry.get("id", ""),
            }
        )

    results.sort(key=lambda r: r["duration_seconds"], reverse=True)
    log.info("YouTube search returned %d full-match candidates", len(results))
    return results


def search_fixtures(
    team1: str,
    team2: str,
    date: str | None = None,
    *,
    season: int | None = None,
) -> list[dict[str, Any]]:
    """Search API-Football for fixtures between *team1* and *team2*.

    Uses each team's schedule for *season* (calendar year the league season
    starts, e.g. ``2025`` for 2025–26) and keeps rows where both team IDs
    appear as home and away. Best-effort — returns an empty list if the API
    is unreachable or the key is not configured.
    """
    if not API_FOOTBALL_KEY:
        log.warning("API_FOOTBALL_KEY not set — skipping fixture search")
        return []

    try:
        team1_id = _resolve_team_id(team1)
        team2_id = _resolve_team_id(team2)
        if team1_id is None or team2_id is None:
            return []

        season_year = season if season is not None else datetime.now().year
        params = f"team={team1_id}&season={season_year}"
        if date:
            params += f"&date={date}"
        raw = _api_get(f"/fixtures?{params}")
        fixtures: list[dict[str, Any]] = []

        for item in raw.get("response", []):
            home_team = item["teams"]["home"]
            away_team = item["teams"]["away"]
            hid = int(home_team["id"])
            aid = int(away_team["id"])
            if {hid, aid} != {team1_id, team2_id}:
                continue
            home = home_team["name"]
            away = away_team["name"]
            fixtures.append(
                {
                    "fixture_id": item["fixture"]["id"],
                    "home_team": home,
                    "away_team": away,
                    "date": item["fixture"]["date"],
                    "league": item["league"]["name"],
                    "score": item.get("goals"),
                }
            )

        log.info("API-Football returned %d fixtures", len(fixtures))
        return fixtures

    except Exception:
        log.exception("API-Football fixture search failed")
        return []


def find_match(
    user_input: str,
    *,
    skip_duration_check: bool = False,
) -> dict[str, Any]:
    """Identify a match from a URL or text query.

    * URL → download video, probe duration, save metadata, return metadata dict.
    * Text → search YouTube, return ``{type: "search_results", candidates: [...]}``.
    """
    if is_url(user_input):
        return _process_url(user_input, skip_duration_check=skip_duration_check)

    candidates = search_youtube(user_input)
    return {"type": "search_results", "candidates": candidates}


def download_and_save(
    url: str,
    *,
    fixture_id: int | None = None,
    skip_duration_check: bool = False,
) -> dict[str, Any]:
    """Download a specific YouTube URL, validate, persist metadata.

    Returns the metadata dict (same shape as ``metadata.json``).
    """
    return _process_url(
        url,
        fixture_id=fixture_id,
        skip_duration_check=skip_duration_check,
    )


# ── Private helpers ─────────────────────────────────────────────────────────


def _process_url(
    url: str,
    *,
    fixture_id: int | None = None,
    skip_duration_check: bool = False,
) -> dict[str, Any]:
    """Shared logic for find_match (URL path) and download_and_save."""
    video_id = _extract_video_id(url)
    workspace = PIPELINE_WORKSPACE / video_id
    metadata_path = workspace / METADATA_FILENAME

    if metadata_path.exists():
        log.info("Cache hit — loading existing metadata for %s", video_id)
        cached: dict[str, Any] = json.loads(metadata_path.read_text())
        return cached

    log.info("Downloading video (id=%s)", video_id)
    workspace.mkdir(parents=True, exist_ok=True)

    video_path = _download_video(url, workspace)

    try:
        duration = get_video_duration(video_path)
    except FFprobeError as exc:
        raise MatchFinderError(str(exc)) from exc

    _validate_duration(duration, skip_check=skip_duration_check)

    metadata: dict[str, Any] = {
        "video_id": video_id,
        "source": url,
        "video_filename": video_path.name,
        "duration_seconds": duration,
        "workspace": str(workspace),
        "fixture_id": fixture_id,
    }

    metadata_path.write_text(json.dumps(metadata, indent=2))
    log.info("Metadata saved to %s", metadata_path)
    return metadata


def _validate_duration(
    duration_seconds: float,
    *,
    skip_check: bool = False,
    min_duration: float = MIN_DURATION_SECONDS,
) -> None:
    """Raise MatchFinderError if the video is too short."""
    if skip_check:
        log.info("Duration check skipped (dev mode)")
        return
    if duration_seconds < min_duration:
        raise MatchFinderError(
            f"Video is too short: {duration_seconds:.0f}s "
            f"({duration_seconds / 60:.1f} min) — minimum is "
            f"{min_duration / 60:.0f} min. Pass skip_duration_check=True "
            f"to override."
        )


def _extract_video_id(url: str) -> str:
    """Ask yt-dlp for the video ID without downloading."""
    try:
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(url, download=False)
            video_id: str = info["id"]  # type: ignore[index]
            return video_id
    except Exception as exc:
        raise MatchFinderError(f"Could not extract video ID from URL: {exc}") from exc


def _download_video(url: str, workspace: Path) -> Path:
    """Download a YouTube video into *workspace* using yt-dlp."""
    log.info("Downloading: %s", url)
    output_template = str(workspace / "%(title)s.%(ext)s")
    ydl_opts: dict[str, Any] = {
        "outtmpl": output_template,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
    except yt_dlp.utils.DownloadError as exc:
        raise MatchFinderError(f"yt-dlp download failed: {exc}") from exc

    video_path = Path(filename)
    if not video_path.exists():
        video_path = video_path.with_suffix(".mp4")

    if not video_path.exists():
        raise MatchFinderError(f"Download appeared to succeed but file not found at {video_path}")

    log.info("Downloaded to %s", video_path)
    return video_path


def _resolve_team_id(name: str) -> int | None:
    """Look up a team's API-Football ID by name."""
    data = _api_get(f"/teams?search={urllib.parse.quote(name)}")
    teams = data.get("response", [])
    if not teams:
        log.warning("No team found for '%s'", name)
        return None
    team_id: int = teams[0]["team"]["id"]
    return team_id


def _api_get(path: str) -> dict[str, Any]:
    """Make a GET request to API-Football and return the parsed JSON."""
    url = f"{API_FOOTBALL_BASE_URL}{path}"
    req = urllib.request.Request(
        url,
        headers={
            "x-rapidapi-key": API_FOOTBALL_KEY,
            "x-rapidapi-host": "v3.football.api-sports.io",
        },
    )
    with urllib.request.urlopen(req) as resp:  # nosec B310
        body: dict[str, Any] = json.loads(resp.read())
    return body
