"""Load curated matches from packaged JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PACKAGE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class CatalogMatch:
    """One catalog entry (video must exist in storage under ``match_id``)."""

    match_id: str
    title: str
    home_team: str
    away_team: str
    competition: str
    season_label: str
    events_snapshot: str
    fixture_id: int | None


def _matches_path() -> Path:
    return _PACKAGE_DIR / "data" / "matches.json"


def load_catalog() -> list[CatalogMatch]:
    """Return all curated matches (order preserved)."""
    raw = json.loads(_matches_path().read_text(encoding="utf-8"))
    out: list[CatalogMatch] = []
    for row in raw["matches"]:
        fid = row.get("fixture_id")
        out.append(
            CatalogMatch(
                match_id=row["match_id"],
                title=row["title"],
                home_team=row["home_team"],
                away_team=row["away_team"],
                competition=row["competition"],
                season_label=row["season_label"],
                events_snapshot=row["events_snapshot"],
                fixture_id=int(fid) if fid is not None else None,
            )
        )
    return out


def list_matches() -> list[dict[str, Any]]:
    """API-friendly dicts (no secrets)."""
    return [
        {
            "match_id": m.match_id,
            "title": m.title,
            "home_team": m.home_team,
            "away_team": m.away_team,
            "competition": m.competition,
            "season_label": m.season_label,
        }
        for m in load_catalog()
    ]


def get_match(match_id: str) -> CatalogMatch | None:
    """Return a single match or ``None`` if id is unknown."""
    mid = match_id.strip()
    for m in load_catalog():
        if m.match_id == mid:
            return m
    return None


def snapshot_json_path(events_snapshot: str) -> Path:
    """Path to bundled ``match_events``-shaped JSON for this snapshot key."""
    return _PACKAGE_DIR / "snapshots" / f"{events_snapshot}.json"
