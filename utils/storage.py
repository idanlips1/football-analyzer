"""Storage backend abstraction — local filesystem and Azure Blob implementations."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class StorageError(Exception):
    """Raised on storage I/O failures (file not found, corrupt JSON, etc.)."""


@runtime_checkable
class StorageBackend(Protocol):
    def read_json(self, video_id: str, filename: str) -> dict[str, Any]: ...
    def write_json(self, video_id: str, filename: str, data: dict[str, Any]) -> None: ...
    def local_path(self, video_id: str, filename: str) -> Path: ...
    # NOTE: local_path returns a real filesystem Path. Azure implementations must
    # download the blob to a temp file first and return that path.
    def workspace_path(self, video_id: str) -> Path: ...
    def list_games(self) -> list[str]: ...
    def upload_file(self, video_id: str, filename: str, local_path: Path) -> None: ...
    def streaming_url(self, video_id: str, filename: str) -> str | None:
        """Return a direct HTTP URL for *filename* (e.g. Azure SAS), or None.

        When available, FFmpeg can read from the URL via HTTP byte-range
        requests instead of requiring a full local download.
        """
        ...


class LocalStorage:
    """Filesystem-backed StorageBackend."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def read_json(self, video_id: str, filename: str) -> dict[str, Any]:
        path = self._root / video_id / filename
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError as exc:
            raise StorageError(f"{filename!r} not found for {video_id!r}") from exc
        except json.JSONDecodeError as exc:
            raise StorageError(f"{filename!r} for {video_id!r} is not valid JSON") from exc
        if not isinstance(data, dict):
            raise StorageError(f"{filename!r} for {video_id!r} is not a JSON object")
        return data

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
        # List uploaded videos (match.mp4 + metadata.json), not necessarily fully ingested.
        out: list[str] = []
        for d in sorted(self._root.iterdir()):
            if not d.is_dir():
                continue
            if (d / "match.mp4").exists() and (d / "metadata.json").exists():
                out.append(d.name)
        return out

    def upload_file(self, video_id: str, filename: str, local_path: Path) -> None:
        dest = self.local_path(video_id, filename)
        if local_path.resolve() != dest.resolve():
            import shutil

            shutil.copy2(local_path, dest)

    def streaming_url(self, video_id: str, filename: str) -> str | None:
        return None


class BlobStorage:
    """Azure Blob-backed StorageBackend.

    Downloads blobs to a local temp directory for FFmpeg compatibility.
    Uploads results back to blob after writes.
    """

    def __init__(
        self,
        connection_string: str,
        container_videos: str = "videos",
        container_pipeline: str = "pipeline",
        container_highlights: str = "highlights",
        temp_root: Path | None = None,
        _blob_service_client: Any | None = None,
    ) -> None:
        if _blob_service_client is not None:
            self._client = _blob_service_client
        else:
            from azure.storage.blob import BlobServiceClient  # type: ignore[import-untyped]

            self._client = BlobServiceClient.from_connection_string(connection_string)
        self._containers = {
            "videos": container_videos,
            "pipeline": container_pipeline,
            "highlights": container_highlights,
        }
        self._temp_root = temp_root or Path(tempfile.gettempdir()) / "football-analyzer"
        self._temp_root.mkdir(parents=True, exist_ok=True)

    def _container_for_file(self, filename: str) -> str:
        """Route files to the correct blob container."""
        if filename.endswith((".mp4", ".wav")) or filename == "metadata.json":
            return self._containers["videos"]
        return self._containers["pipeline"]

    def read_json(self, video_id: str, filename: str) -> dict[str, Any]:
        container_name = self._container_for_file(filename)
        container = self._client.get_container_client(container_name)
        blob = container.get_blob_client(f"{video_id}/{filename}")
        try:
            data = json.loads(blob.download_blob().readall())
        except Exception as exc:
            raise StorageError(f"{filename!r} not found for {video_id!r}") from exc
        if not isinstance(data, dict):
            raise StorageError(f"{filename!r} for {video_id!r} is not a JSON object")
        return data

    def write_json(self, video_id: str, filename: str, data: dict[str, Any]) -> None:
        container_name = self._container_for_file(filename)
        container = self._client.get_container_client(container_name)
        blob_data = json.dumps(data, indent=2)
        container.upload_blob(f"{video_id}/{filename}", blob_data, overwrite=True)
        # Also write locally to avoid re-downloading in the same job run
        local = self._temp_root / video_id / filename
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(blob_data)

    def local_path(self, video_id: str, filename: str) -> Path:
        local = self._temp_root / video_id / filename
        if local.exists():
            return local
        local.parent.mkdir(parents=True, exist_ok=True)
        container_name = self._container_for_file(filename)
        container = self._client.get_container_client(container_name)
        blob = container.get_blob_client(f"{video_id}/{filename}")
        try:
            stream = blob.download_blob()
            with open(local, "wb") as f:
                stream.readinto(f)
        except Exception as exc:  # noqa: BLE001
            # Clean up partial downloads
            if local.exists():
                local.unlink()
            # Fail fast: callers expect a real file path for FFmpeg.
            # Returning a non-existent temp path causes confusing downstream FFmpeg errors.
            raise StorageError(f"Failed to download {filename!r} for {video_id!r}: {exc}") from exc
        if not local.exists() or local.stat().st_size == 0:
            raise StorageError(
                f"Downloaded {filename!r} for {video_id!r} is missing/empty at {local}"
            )
        return local

    def workspace_path(self, video_id: str) -> Path:
        path = self._temp_root / video_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def list_games(self) -> list[str]:
        # List uploaded videos in the videos container (match.mp4 + metadata.json).
        container = self._client.get_container_client(self._containers["videos"])
        blobs = container.list_blobs()
        files_by_video: dict[str, set[str]] = {}
        for blob in blobs:
            parts = blob.name.split("/", 1)
            if len(parts) != 2:
                continue
            vid, fname = parts
            files_by_video.setdefault(vid, set()).add(fname)
        return sorted(
            vid
            for vid, files in files_by_video.items()
            if "match.mp4" in files and "metadata.json" in files
        )

    def upload_file(self, video_id: str, filename: str, local_path: Path) -> None:
        container_name = self._container_for_file(filename)
        container = self._client.get_container_client(container_name)
        blob_name = f"{video_id}/{filename}"
        with open(local_path, "rb") as f:
            container.upload_blob(blob_name, f, overwrite=True)

        # Write locally as well to cache it
        local = self._temp_root / video_id / filename
        local.parent.mkdir(parents=True, exist_ok=True)
        if local_path.resolve() != local.resolve():
            import shutil

            shutil.copy2(local_path, local)

    def upload_highlights(self, video_id: str, query_hash: str, local_file: Path) -> str:
        """Upload a highlights file and return the blob name."""
        container = self._client.get_container_client(self._containers["highlights"])
        blob_name = f"{video_id}/{query_hash}.mp4"
        with open(local_file, "rb") as f:
            container.upload_blob(blob_name, f, overwrite=True)
        return blob_name

    def streaming_url(self, video_id: str, filename: str) -> str | None:
        """Generate a short-lived SAS URL so FFmpeg can read via HTTP byte-ranges."""
        from datetime import UTC, datetime, timedelta

        from azure.storage.blob import (  # type: ignore[import-untyped]
            BlobSasPermissions,
            generate_blob_sas,
        )

        container_name = self._container_for_file(filename)
        blob_name = f"{video_id}/{filename}"
        account_name = self._client.account_name
        account_key = self._client.credential.account_key
        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=container_name,
            blob_name=blob_name,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(UTC) + timedelta(hours=4),
        )
        return (
            f"https://{account_name}.blob.core.windows.net/"
            f"{container_name}/{blob_name}?{sas_token}"
        )

    def generate_sas_url(self, blob_name: str, expiry_hours: int = 24) -> str:
        """Generate a SAS URL for a highlights blob."""
        from datetime import UTC, datetime, timedelta

        from azure.storage.blob import (  # type: ignore[import-untyped]
            BlobSasPermissions,
            generate_blob_sas,
        )

        container_name = self._containers["highlights"]
        account_name = self._client.account_name
        account_key = self._client.credential.account_key
        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=container_name,
            blob_name=blob_name,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(UTC) + timedelta(hours=expiry_hours),
        )
        return (
            f"https://{account_name}.blob.core.windows.net/{container_name}/{blob_name}?{sas_token}"
        )

    def cleanup_temp(self, video_id: str) -> None:
        """Remove temp directory for a video_id."""
        temp_dir = self._temp_root / video_id
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
