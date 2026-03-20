"""Stage 1 (new) — Match finder: YouTube search, API-Football fixture lookup,
and video download with metadata persistence."""

from __future__ import annotations

import json
import re
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
# UEFA Champions League (API-Football league id)
_LEAGUE_CHAMPIONS_LEAGUE = 2


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


def fetch_video_title(url: str) -> str:
    """Return the YouTube title without downloading the video."""
    try:
        with yt_dlp.YoutubeDL(
            {"quiet": True, "no_warnings": True, "skip_download": True},
        ) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            return ""
        return str(info.get("title") or "")
    except Exception:
        log.exception("Could not fetch video title for fixture lookup")
        return ""


def parse_teams_from_video_title(title: str) -> tuple[str, str] | None:
    """Extract two club names from a typical full-match upload title."""
    t = title.strip()
    if "|" in t:
        t = t.split("|", 1)[0].strip()
    t = re.sub(r"\s*\([^)]*\)\s*$", "", t).strip()

    m_score = re.match(r"^(.+?)\s+\d+\s*-\s*\d+\s+(.+)$", t)
    if m_score:
        return m_score.group(1).strip(), m_score.group(2).strip()

    m_vs = re.match(r"^(.+?)\s+(?:vs\.?|v\.?)\s+(.+)$", t, re.IGNORECASE)
    if m_vs:
        return m_vs.group(1).strip(), m_vs.group(2).strip()

    return None


def extract_years_from_text(text: str) -> list[int]:
    """Collect plausible calendar years (20xx) from *text*."""
    return [int(y) for y in re.findall(r"\b(20\d{2})\b", text)]


def infer_league_id_from_query(query: str) -> int | None:
    """Map free-text *query* to an API-Football league id when obvious."""
    q = query.lower()
    if "champions league" in q or re.search(r"\bucl\b", q):
        return _LEAGUE_CHAMPIONS_LEAGUE
    return None


def _fixture_row_from_api_item(item: dict[str, Any]) -> dict[str, Any]:
    home_team = item["teams"]["home"]
    away_team = item["teams"]["away"]
    league = item.get("league") or {}
    return {
        "fixture_id": item["fixture"]["id"],
        "home_team": home_team["name"],
        "away_team": away_team["name"],
        "date": item["fixture"]["date"],
        "league": league.get("name", ""),
        "league_id": int(league["id"]) if league.get("id") is not None else 0,
        "score": item.get("goals"),
    }


def _fixture_date_year(iso_date: str) -> int:
    return int(str(iso_date)[:4])


def fetch_headtohead_fixtures(
    team1: str,
    team2: str,
    *,
    season: int | None = None,
    league: int | None = None,
    last: int = 80,
) -> list[dict[str, Any]]:
    """Head-to-head fixtures between two team names (resolved via search)."""
    if not API_FOOTBALL_KEY:
        log.warning("API_FOOTBALL_KEY not set — skipping head-to-head lookup")
        return []

    try:
        team1_id = _resolve_team_id(team1)
        team2_id = _resolve_team_id(team2)
        if team1_id is None or team2_id is None:
            return []

        parts = [f"h2h={team1_id}-{team2_id}", f"last={last}"]
        if season is not None:
            parts.append(f"season={season}")
        if league is not None:
            parts.append(f"league={league}")
        raw = _api_get(f"/fixtures/headtohead?{'&'.join(parts)}")
        rows: list[dict[str, Any]] = []
        for item in raw.get("response", []):
            rows.append(_fixture_row_from_api_item(item))
        log.info("Head-to-head returned %d fixtures", len(rows))
        return rows

    except Exception:
        log.exception("API-Football head-to-head lookup failed")
        return []


def resolve_fixture_for_video(
    user_query: str,
    video_title: str,
) -> tuple[int | None, list[dict[str, Any]]]:
    """Pick a single fixture id from the video title and query, or return candidates.

    Returns ``(fixture_id, [])`` when unique; ``(None, rows)`` when the user
    should choose; ``(None, [])`` when parsing or API lookup failed.
    """
    teams = parse_teams_from_video_title(video_title)
    if not teams:
        log.info("Could not parse two teams from title: %s", video_title[:80])
        return None, []

    a, b = teams
    league_hint = infer_league_id_from_query(user_query + " " + video_title)
    years = extract_years_from_text(user_query + " " + video_title)
    year_set = set(years)
    for y in list(years):
        year_set.add(y - 1)

    rows = fetch_headtohead_fixtures(a, b, league=league_hint, last=100)
    if not rows and league_hint is not None:
        rows = fetch_headtohead_fixtures(a, b, last=100)

    if not rows:
        return None, []

    def sort_key(r: dict[str, Any]) -> str:
        return str(r["date"])

    rows_sorted = sorted(rows, key=sort_key, reverse=True)

    if year_set:
        filtered = [r for r in rows_sorted if _fixture_date_year(str(r["date"])) in year_set]
        if len(filtered) == 1:
            return int(filtered[0]["fixture_id"]), []
        if len(filtered) > 1:
            return None, sorted(filtered, key=sort_key, reverse=True)
        # No row matched listed years — offer the most recent meetings instead
        return None, rows_sorted[:12]

    if len(rows_sorted) == 1:
        return int(rows_sorted[0]["fixture_id"]), []

    return None, rows_sorted[:12]


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
            fixtures.append(_fixture_row_from_api_item(item))

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
