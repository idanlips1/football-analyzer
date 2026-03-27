"""GameRegistry — discovers and loads ready games from storage."""

from __future__ import annotations

from models.game import GameState
from utils.storage import StorageBackend


class GameRegistry:
    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    def list_ready(self) -> list[GameState]:
        """Return GameState for each fully ingested game (see ``StorageBackend.list_games``)."""
        games: list[GameState] = []
        for video_id in self._storage.list_games():
            data = self._storage.read_json(video_id, "game.json")
            games.append(GameState.from_dict(data))
        return games
