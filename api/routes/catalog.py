"""Curated matches catalog API."""

from __future__ import annotations

from fastapi import APIRouter

from catalog.loader import list_matches

router = APIRouter()


@router.get("/matches")
async def get_matches() -> dict:
    """List matches available for processing (video must exist in storage)."""
    return {"matches": list_matches()}
