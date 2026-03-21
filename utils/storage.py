"""Storage backend abstraction — local filesystem implementation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    def read_json(self, video_id: str, filename: str) -> dict[str, Any]: ...
    def write_json(self, video_id: str, filename: str, data: dict[str, Any]) -> None: ...
    def local_path(self, video_id: str, filename: str) -> Path: ...
    def workspace_path(self, video_id: str) -> Path: ...
    def list_games(self) -> list[str]: ...


class LocalStorage:
    """Filesystem-backed StorageBackend. Root defaults to PIPELINE_WORKSPACE."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def read_json(self, video_id: str, filename: str) -> dict[str, Any]:
        return json.loads((self._root / video_id / filename).read_text())  # type: ignore[no-any-return]

    def write_json(self, video_id: str, filename: str, data: dict[str, Any]) -> None:
        ws = self._root / video_id
        ws.mkdir(parents=True, exist_ok=True)
        (ws / filename).write_text(json.dumps(data, indent=2))

    def local_path(self, video_id: str, filename: str) -> Path:
        return self._root / video_id / filename

    def workspace_path(self, video_id: str) -> Path:
        path = self._root / video_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def list_games(self) -> list[str]:
        if not self._root.exists():
            return []
        return [
            d.name
            for d in sorted(self._root.iterdir())
            if d.is_dir() and (d / "game.json").exists() and (d / "aligned_events.json").exists()
        ]
