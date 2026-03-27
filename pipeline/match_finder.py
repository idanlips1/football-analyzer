"""API-Football fixture lookup and video title parsing (no YouTube downloads).

Stage-1 video acquisition for production is via :mod:`pipeline.catalog_pipeline`
and blob storage; see ``catalog/`` and ``scripts/upload_catalog_match.py``.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

from config.settings import (
    API_FOOTBALL_BASE_URL,
    API_FOOTBALL_KEY,
)
from utils.logger import get_logger
from utils.storage import StorageBackend

log = get_logger(__name__)

METADATA_FILENAME = "metadata.json"
# UEFA Champions League (API-Football league id)
_LEAGUE_CHAMPIONS_LEAGUE = 2
_LEAGUE_EUROPA_LEAGUE = 3
_LEAGUE_FA_CUP = 45
_LEAGUE_PREMIER_LEAGUE = 39
_LEAGUE_LA_LIGA = 140
_LEAGUE_WORLD_CUP = 1


class MatchFinderError(Exception):
    """Raised when match finding helpers fail (legacy name)."""


# ── Public API ──────────────────────────────────────────────────────────────


def is_url(text: str) -> bool:
    """Return True if *text* looks like an HTTP(S) URL."""
    return text.startswith("http://") or text.startswith("https://")


_YT_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?.*?v=|embed/|v/|shorts/)|youtu\.be/)"
    r"([a-zA-Z0-9_-]{11})"
)


def extract_video_id_from_url(url: str) -> str | None:
    """Extract the YouTube video ID from a URL string without network calls."""
    m = _YT_ID_RE.search(url)
    return m.group(1) if m else None


def load_existing_metadata(video_id: str, storage: StorageBackend) -> dict[str, Any] | None:
    """Load metadata for an already-downloaded video, or ``None``."""
    cache_path = storage.local_path(video_id, METADATA_FILENAME)
    if cache_path.exists():
        return storage.read_json(video_id, METADATA_FILENAME)
    return None


class TitleParseResult:
    """Parsed components from a video title."""

    __slots__ = ("team_a", "team_b", "score_home", "score_away")

    def __init__(
        self,
        team_a: str,
        team_b: str,
        score_home: int | None = None,
        score_away: int | None = None,
    ) -> None:
        self.team_a = team_a
        self.team_b = team_b
        self.score_home = score_home
        self.score_away = score_away

    @property
    def has_score(self) -> bool:
        return self.score_home is not None and self.score_away is not None

    @property
    def teams(self) -> tuple[str, str]:
        return self.team_a, self.team_b


_PIPE_RE = re.compile(r"\s*[|｜]\s*")


def _parse_single_segment(t: str) -> TitleParseResult | None:
    """Try to extract teams (and optional score) from a single title segment."""
    if not t:
        return None

    # Strip a leading competition/context prefix like "2018 FIFA World Cup: "
    colon_m = re.match(r"^[^:]+:\s*(.+)$", t)
    if colon_m:
        after_colon = colon_m.group(1).strip()
        # Only use the part after the colon if it still contains a vs/score separator.
        if re.search(r"\b(?:vs\.?|v\.?)\b|\d+\s*-\s*\d+", after_colon, re.IGNORECASE):
            t = after_colon

    paren_score: tuple[int, int] | None = None
    paren_m = re.search(r"\((\d+)\s*-\s*(\d+)\)", t)
    if paren_m:
        paren_score = (int(paren_m.group(1)), int(paren_m.group(2)))
        t = (t[: paren_m.start()] + t[paren_m.end() :]).strip()

    m_score = re.match(r"^(.+?)\s+(\d+)\s*-\s*(\d+)\s+(.+)$", t)
    if m_score:
        return TitleParseResult(
            team_a=m_score.group(1).strip(),
            team_b=m_score.group(4).strip(),
            score_home=int(m_score.group(2)),
            score_away=int(m_score.group(3)),
        )

    m_vs = re.match(r"^(.+?)\s+(?:vs\.?|v\.?)\s+(.+)$", t, re.IGNORECASE)
    if m_vs:
        return TitleParseResult(
            team_a=m_vs.group(1).strip(),
            team_b=m_vs.group(2).strip(),
            score_home=paren_score[0] if paren_score else None,
            score_away=paren_score[1] if paren_score else None,
        )

    return None


def parse_video_title(title: str) -> TitleParseResult | None:
    """Extract teams and optional score from a typical full-match upload title.

    Tries each pipe-delimited segment so that titles like
    ``"MBAPPE VS. MESSI | 2018 FIFA World Cup: France v Argentina"``
    yield *France* / *Argentina* (from the second segment) rather than
    player names from the first.
    """
    t = title.strip()
    segments = _PIPE_RE.split(t)

    if segments and re.match(r"^(?:FULL\s+MATCH|HIGHLIGHTS?)$", segments[0], re.IGNORECASE):
        segments = segments[1:]

    # Collect a parse result from every segment, then pick the best one.
    results: list[TitleParseResult] = []
    for seg in segments:
        r = _parse_single_segment(seg.strip())
        if r is not None:
            results.append(r)

    if not results:
        return None
    if len(results) == 1:
        return results[0]

    # Prefer the result whose team names are longer (more likely real team names
    # rather than player names like "Mbappe" / "Messi").
    return max(results, key=lambda r: len(r.team_a) + len(r.team_b))


def parse_teams_from_video_title(title: str) -> tuple[str, str] | None:
    """Extract two club names from a typical full-match upload title."""
    result = parse_video_title(title)
    if result is None:
        return None
    return result.teams


def extract_years_from_text(text: str) -> list[int]:
    """Collect plausible calendar years (20xx) from *text*.

    Also handles shorthand seasons like "2023-24" → [2023, 2024].
    """
    years: list[int] = []
    # Match "2023-24" style season shorthand first.
    for full, _, short in re.findall(r"\b(20(\d{2}))-(\d{2})\b", text):
        years.append(int(full))
        years.append(2000 + int(short))
    # Then standalone 4-digit years, excluding those already captured in a season range.
    cleaned = re.sub(r"\b20\d{2}-\d{2}\b", "", text)
    years.extend(int(y) for y in re.findall(r"\b(20\d{2})\b", cleaned))
    return years


def infer_league_id_from_query(query: str) -> int | None:
    """Map free-text *query* to an API-Football league id when obvious."""
    q = query.lower()
    if "champions league" in q or re.search(r"\bucl\b", q):
        return _LEAGUE_CHAMPIONS_LEAGUE
    if "europa league" in q or re.search(r"\buel\b", q):
        return _LEAGUE_EUROPA_LEAGUE
    if "fa cup" in q or "emirates fa" in q:
        return _LEAGUE_FA_CUP
    if "premier league" in q or re.search(r"\bepl\b", q):
        return _LEAGUE_PREMIER_LEAGUE
    if "la liga" in q or "laliga" in q:
        return _LEAGUE_LA_LIGA
    if "world cup" in q or re.search(r"\bfifa\b", q):
        return _LEAGUE_WORLD_CUP
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

        parts = [f"h2h={team1_id}-{team2_id}"]
        if season is not None:
            parts.append(f"season={season}")
        if league is not None:
            parts.append(f"league={league}")
        raw = _api_get(f"/fixtures/headtohead?{'&'.join(parts)}")

        api_errors = raw.get("errors")
        if api_errors:
            log.warning("API-Football H2H errors: %s", api_errors)

        rows: list[dict[str, Any]] = []
        for item in raw.get("response", []):
            rows.append(_fixture_row_from_api_item(item))
        log.info("Head-to-head returned %d fixtures", len(rows))
        return rows

    except Exception:
        log.exception("API-Football head-to-head lookup failed")
        return []


class FixtureResolution:
    """Result of automatic fixture resolution."""

    __slots__ = (
        "fixture_id",
        "fixture_row",
        "candidates",
        "teams_parsed",
        "team_a",
        "team_b",
    )

    def __init__(
        self,
        *,
        fixture_id: int | None = None,
        fixture_row: dict[str, Any] | None = None,
        candidates: list[dict[str, Any]] | None = None,
        teams_parsed: bool = False,
        team_a: str = "",
        team_b: str = "",
    ) -> None:
        self.fixture_id = fixture_id
        self.fixture_row = fixture_row
        self.candidates = candidates or []
        self.teams_parsed = teams_parsed
        self.team_a = team_a
        self.team_b = team_b


def _score_matches(
    row: dict[str, Any],
    title_home: int,
    title_away: int,
) -> bool:
    goals = row.get("score")
    if not isinstance(goals, dict):
        return False
    gh, ga = goals.get("home"), goals.get("away")
    if gh is None or ga is None:
        return False
    return bool((gh == title_home and ga == title_away) or (gh == title_away and ga == title_home))


def resolve_fixture_for_video(
    user_query: str,
    video_title: str,
    *,
    upload_year: int | None = None,
) -> FixtureResolution:
    """Pick a single fixture id from the video title and query, or return candidates."""
    parsed = parse_video_title(video_title)
    if not parsed:
        log.info("Could not parse two teams from title: %s", video_title[:80])
        return FixtureResolution()

    a, b = parsed.teams
    league_hint = infer_league_id_from_query(user_query + " " + video_title)
    years = extract_years_from_text(user_query + " " + video_title)
    year_set = set(years)
    for y in list(years):
        year_set.add(y - 1)

    if not year_set and upload_year is not None:
        log.info("No year in title/query — using upload year %d as hint", upload_year)
        year_set = {upload_year, upload_year - 1}

    rows = fetch_headtohead_fixtures(a, b, league=league_hint)
    if not rows and league_hint is not None:
        rows = fetch_headtohead_fixtures(a, b)

    base = FixtureResolution(teams_parsed=True, team_a=a, team_b=b)

    if not rows:
        return base

    def sort_key(r: dict[str, Any]) -> str:
        return str(r["date"])

    rows_sorted = sorted(rows, key=sort_key, reverse=True)
    pool = rows_sorted

    if year_set:
        year_filtered = [r for r in pool if _fixture_date_year(str(r["date"])) in year_set]
        if year_filtered:
            pool = year_filtered

    if parsed.has_score:
        score_filtered = [
            r
            for r in pool
            if _score_matches(r, parsed.score_home, parsed.score_away)  # type: ignore[arg-type]
        ]
        if score_filtered:
            log.info(
                "Score filter (%d-%d) narrowed %d → %d fixtures",
                parsed.score_home,
                parsed.score_away,
                len(pool),
                len(score_filtered),
            )
            pool = score_filtered

    if len(pool) == 1:
        base.fixture_id = int(pool[0]["fixture_id"])
        base.fixture_row = pool[0]
        return base

    if len(pool) > 1:
        base.candidates = sorted(pool, key=sort_key, reverse=True)[:12]
        return base

    if len(rows_sorted) == 1:
        base.fixture_id = int(rows_sorted[0]["fixture_id"])
        base.fixture_row = rows_sorted[0]
        return base

    base.candidates = rows_sorted[:12]
    return base


def search_fixtures(
    team1: str,
    team2: str,
    date: str | None = None,
    *,
    season: int | None = None,
) -> list[dict[str, Any]]:
    """Search API-Football for fixtures between *team1* and *team2*."""
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


def _resolve_team_id(name: str) -> int | None:
    """Look up a team's API-Football ID by name."""
    log.info("Resolving team ID for '%s'…", name)
    data = _api_get(f"/teams?search={urllib.parse.quote(name)}")
    teams = data.get("response", [])
    if not teams:
        log.warning("No team found for '%s'", name)
        return None
    team_id: int = teams[0]["team"]["id"]
    log.info("Team '%s' → id=%d", name, team_id)
    return team_id


def _api_get(path: str) -> dict[str, Any]:
    """Make a GET request to API-Football and return the parsed JSON."""
    url = f"{API_FOOTBALL_BASE_URL}{path}"
    log.debug("API-Football GET %s", path)
    req = urllib.request.Request(
        url,
        headers={
            "x-rapidapi-key": API_FOOTBALL_KEY,
            "x-rapidapi-host": "v3.football.api-sports.io",
        },
    )
    t0 = time.monotonic()
    with urllib.request.urlopen(req) as resp:  # nosec B310
        body: dict[str, Any] = json.loads(resp.read())
    elapsed = time.monotonic() - t0
    log.info("API-Football %s responded in %.1f s", path.split("?")[0], elapsed)
    return body
