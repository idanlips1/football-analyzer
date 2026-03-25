"""Curated matches catalog API."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies import get_storage
from catalog.loader import list_matches
from utils.storage import StorageBackend

router = APIRouter()


@router.get("/matches")
async def get_matches(storage: StorageBackend = Depends(get_storage)) -> dict:  # noqa: B008
    """List matches available for processing (video must exist in storage)."""
    available_ids = set(storage.list_games())
    matches = [m for m in list_matches() if m["match_id"] in available_ids]
    return {"matches": matches}
