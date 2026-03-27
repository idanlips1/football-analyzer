"""Matches API: list queryable games available in storage."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies import get_storage
from utils.storage import StorageBackend

router = APIRouter()


@router.get("/matches")
async def get_matches(storage: StorageBackend = Depends(get_storage)) -> dict:  # noqa: B008
    """List matches available for processing (must exist in storage)."""
    ids = storage.list_games()
    return {"matches": [{"match_id": mid} for mid in ids]}
