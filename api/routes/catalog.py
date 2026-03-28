"""Matches API: list queryable games available in storage."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies import get_storage
from catalog.loader import list_matches
from utils.storage import StorageBackend

router = APIRouter()


@router.get("/matches")
async def get_matches(storage: StorageBackend = Depends(get_storage)) -> dict:  # noqa: B008
    """List matches available for processing (must exist in both catalog and storage)."""
    storage_ids = set(storage.list_games())
    enriched = [m for m in list_matches() if m["match_id"] in storage_ids]
    return {"matches": enriched}
