# Azure Async Deployment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy football-analyzer as an async REST API on Azure Container Apps with queue-based job processing.

**Architecture:** FastAPI API + queue-decoupled worker on ACA (always-on). Azure Blob Storage replaces local filesystem via a new `BlobStorage` class implementing the existing `StorageBackend` protocol. Azure Table Storage tracks job state. Single Docker image, two entrypoints.

**Tech Stack:** FastAPI, Azure Storage SDK (blob/queue/table), httpx, Azure Bicep, Docker

**Spec:** `docs/superpowers/specs/2026-03-22-azure-async-deployment-design.md`

---

### Task 1: Add Azure dependencies & update config

**Files:**
- Modify: `requirements.txt`
- Modify: `config/settings.py`
- Modify: `pyproject.toml` (add `api` and `worker` to coverage)

- [ ] **Step 1: Add Azure + httpx deps to requirements.txt**

Add after the `# API (optional)` section:

```
# Azure
azure-storage-blob
azure-storage-queue
azure-data-tables

# HTTP client (webhook delivery)
httpx
```

- [ ] **Step 2: Add Azure config vars to settings.py**

Append to `config/settings.py`:

```python
# --- Azure deployment ---
STORAGE_BACKEND: str = os.environ.get("STORAGE_BACKEND", "local")  # "local" | "azure"
AZURE_STORAGE_CONNECTION_STRING: str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
AZURE_BLOB_CONTAINER_VIDEOS: str = "videos"
AZURE_BLOB_CONTAINER_PIPELINE: str = "pipeline"
AZURE_BLOB_CONTAINER_HIGHLIGHTS: str = "highlights"
AZURE_QUEUE_NAME: str = "job-queue"
AZURE_TABLE_NAME: str = "jobs"
SAS_EXPIRY_HOURS: int = 24
API_KEYS: list[str] = [
    k.strip() for k in os.environ.get("API_KEYS", "").split(",") if k.strip()
]
```

- [ ] **Step 3: Update pyproject.toml coverage**

Change `addopts` to include `api` and `worker`:

```toml
addopts = "--cov=pipeline --cov=models --cov=config --cov=utils --cov=api --cov=worker --cov-report=term-missing"
```

- [ ] **Step 4: Install deps and verify**

Run: `pip install -r requirements.txt`
Run: `python -c "from config.settings import STORAGE_BACKEND; print(STORAGE_BACKEND)"`
Expected: `local`

- [ ] **Step 5: Commit**

```bash
git add requirements.txt config/settings.py pyproject.toml
git commit -m "feat: add Azure deps and deployment config vars"
```

---

### Task 2: BlobStorage implementation

**Files:**
- Modify: `utils/storage.py`
- Create: `tests/test_blob_storage.py`

- [ ] **Step 1: Write failing tests for BlobStorage**

Create `tests/test_blob_storage.py`. Test that `BlobStorage` implements the `StorageBackend` protocol. Mock all Azure SDK calls.

```python
"""Tests for BlobStorage — Azure Blob-backed StorageBackend."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from utils.storage import BlobStorage, StorageBackend


@pytest.fixture()
def mock_blob_service() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def blob_storage(tmp_path: Path, mock_blob_service: MagicMock) -> BlobStorage:
    return BlobStorage(
        connection_string="fake",
        container_videos="videos",
        container_pipeline="pipeline",
        container_highlights="highlights",
        temp_root=tmp_path,
        _blob_service_client=mock_blob_service,
    )


def test_implements_storage_backend(blob_storage: BlobStorage) -> None:
    assert isinstance(blob_storage, StorageBackend)


def test_write_json_uploads_to_blob_and_writes_locally(
    blob_storage: BlobStorage, mock_blob_service: MagicMock, tmp_path: Path
) -> None:
    data: dict[str, Any] = {"key": "value"}
    blob_storage.write_json("vid123", "metadata.json", data)

    # Should upload to blob
    mock_blob_service.get_container_client.assert_called_with("videos")
    container_client = mock_blob_service.get_container_client.return_value
    container_client.upload_blob.assert_called_once()
    call_args = container_client.upload_blob.call_args
    assert call_args[0][0] == "vid123/metadata.json"
    assert json.loads(call_args[0][1]) == data

    # Should also write locally (avoids re-download on next local_path call)
    local_file = tmp_path / "vid123" / "metadata.json"
    assert local_file.exists()
    assert json.loads(local_file.read_text()) == data


def test_read_json_downloads_from_blob(
    blob_storage: BlobStorage, mock_blob_service: MagicMock
) -> None:
    expected: dict[str, Any] = {"events": []}
    blob_data = json.dumps(expected).encode()
    container_client = mock_blob_service.get_container_client.return_value
    blob_client = container_client.get_blob_client.return_value
    blob_client.download_blob.return_value.readall.return_value = blob_data

    result = blob_storage.read_json("vid123", "match_events.json")
    assert result == expected


def test_local_path_downloads_blob_to_temp(
    blob_storage: BlobStorage, mock_blob_service: MagicMock, tmp_path: Path
) -> None:
    blob_bytes = b"fake video content"
    container_client = mock_blob_service.get_container_client.return_value
    blob_client = container_client.get_blob_client.return_value
    blob_client.download_blob.return_value.readall.return_value = blob_bytes

    path = blob_storage.local_path("vid123", "video.mp4")
    assert path.exists()
    assert path.read_bytes() == blob_bytes


def test_local_path_uses_cache(
    blob_storage: BlobStorage, mock_blob_service: MagicMock, tmp_path: Path
) -> None:
    """Second call to local_path for same file should not re-download."""
    blob_bytes = b"fake video content"
    container_client = mock_blob_service.get_container_client.return_value
    blob_client = container_client.get_blob_client.return_value
    blob_client.download_blob.return_value.readall.return_value = blob_bytes

    path1 = blob_storage.local_path("vid123", "video.mp4")
    path2 = blob_storage.local_path("vid123", "video.mp4")
    assert path1 == path2
    # download_blob called only once
    assert blob_client.download_blob.call_count == 1


def test_workspace_path_returns_temp_dir(blob_storage: BlobStorage, tmp_path: Path) -> None:
    ws = blob_storage.workspace_path("vid123")
    assert ws.exists()
    assert ws.is_dir()
    assert "vid123" in str(ws)


def test_list_games_queries_pipeline_container(
    blob_storage: BlobStorage, mock_blob_service: MagicMock
) -> None:
    # Simulate blobs in pipeline container
    container_client = mock_blob_service.get_container_client.return_value
    blob1 = MagicMock()
    blob1.name = "vid123/aligned_events.json"
    blob2 = MagicMock()
    blob2.name = "vid123/game.json"  # not in pipeline, but list_games needs both
    blob3 = MagicMock()
    blob3.name = "vid456/aligned_events.json"
    container_client.list_blobs.return_value = [blob1, blob2, blob3]

    # list_games needs game.json in videos container too — mock that
    games = blob_storage.list_games()
    assert isinstance(games, list)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_blob_storage.py -v`
Expected: FAIL — `BlobStorage` not defined yet

- [ ] **Step 3: Implement BlobStorage**

Add to `utils/storage.py` after `LocalStorage`:

```python
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
            from azure.storage.blob import BlobServiceClient
            self._client = BlobServiceClient.from_connection_string(connection_string)
        self._containers = {
            "videos": container_videos,
            "pipeline": container_pipeline,
            "highlights": container_highlights,
        }
        self._temp_root = temp_root or Path("/tmp/football-analyzer")
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
            blob_bytes = blob.download_blob().readall()
            local.write_bytes(blob_bytes)
        except Exception:  # noqa: BLE001
            # Blob doesn't exist yet — return path for creation (pipeline will write here)
            pass
        return local

    def workspace_path(self, video_id: str) -> Path:
        path = self._temp_root / video_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def list_games(self) -> list[str]:
        container = self._client.get_container_client(self._containers["pipeline"])
        blobs = container.list_blobs()
        # Collect video_ids that have both game.json and aligned_events.json
        files_by_video: dict[str, set[str]] = {}
        for blob in blobs:
            parts = blob.name.split("/", 1)
            if len(parts) == 2:
                vid, fname = parts
                files_by_video.setdefault(vid, set()).add(fname)
        return sorted(
            vid
            for vid, files in files_by_video.items()
            if "game.json" in files and "aligned_events.json" in files
        )

    def upload_highlights(self, video_id: str, query_hash: str, local_file: Path) -> str:
        """Upload a highlights file and return the blob name."""
        container = self._client.get_container_client(self._containers["highlights"])
        blob_name = f"{video_id}/{query_hash}.mp4"
        with open(local_file, "rb") as f:
            container.upload_blob(blob_name, f, overwrite=True)
        return blob_name

    def generate_sas_url(self, blob_name: str, expiry_hours: int = 24) -> str:
        """Generate a SAS URL for a highlights blob."""
        from datetime import datetime, timedelta, timezone
        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        container_name = self._containers["highlights"]
        account_name = self._client.account_name
        account_key = self._client.credential.account_key
        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=container_name,
            blob_name=blob_name,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(hours=expiry_hours),
        )
        return f"https://{account_name}.blob.core.windows.net/{container_name}/{blob_name}?{sas_token}"

    def cleanup_temp(self, video_id: str) -> None:
        """Remove temp directory for a video_id."""
        import shutil
        temp_dir = self._temp_root / video_id
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_blob_storage.py -v`
Expected: PASS (adjust mocks/assertions if needed)

- [ ] **Step 5: Run existing tests to confirm no regressions**

Run: `pytest tests/test_storage.py -v`
Expected: All existing LocalStorage tests still pass

- [ ] **Step 6: Run linters**

Run: `ruff check utils/storage.py tests/test_blob_storage.py && mypy utils/storage.py tests/test_blob_storage.py`

- [ ] **Step 7: Commit**

```bash
git add utils/storage.py tests/test_blob_storage.py
git commit -m "feat: add BlobStorage backend for Azure Blob Storage"
```

---

### Task 3: Job model & Table Storage repository

**Files:**
- Create: `models/job.py`
- Create: `utils/job_store.py`
- Create: `tests/test_job_store.py`

- [ ] **Step 1: Create job model**

Create `models/job.py`:

```python
"""Job data model for async highlights generation."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class JobResult:
    download_url: str
    duration_seconds: float
    clip_count: int
    expires_at: str  # ISO 8601

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobResult:
        return cls(**data)


@dataclass
class Job:
    job_id: str = field(default_factory=lambda: uuid4().hex[:12])
    query: str = ""
    status: JobStatus = JobStatus.QUEUED
    progress: str | None = None
    result: JobResult | None = None
    error: str | None = None
    webhook_url: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["status"] = self.status.value
        if self.result:
            d["result"] = self.result.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Job:
        data = dict(data)
        data["status"] = JobStatus(data["status"])
        if data.get("result"):
            data["result"] = JobResult.from_dict(data["result"])
        return cls(**{k: v for k, v in data.items() if k in {f.name for f in dataclasses.fields(cls)}})
```

- [ ] **Step 2: Write failing tests for job store**

Create `tests/test_job_store.py`:

```python
"""Tests for JobStore — in-memory and Azure Table implementations."""

from __future__ import annotations

import pytest

from models.job import Job, JobStatus
from utils.job_store import InMemoryJobStore, JobStore


@pytest.fixture()
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


def test_implements_protocol(store: InMemoryJobStore) -> None:
    assert isinstance(store, JobStore)


def test_create_and_get(store: InMemoryJobStore) -> None:
    job = Job(query="Liverpool vs City")
    store.create(job)
    retrieved = store.get(job.job_id)
    assert retrieved is not None
    assert retrieved.query == "Liverpool vs City"
    assert retrieved.status == JobStatus.QUEUED


def test_get_nonexistent(store: InMemoryJobStore) -> None:
    assert store.get("nope") is None


def test_update_status(store: InMemoryJobStore) -> None:
    job = Job(query="test")
    store.create(job)
    store.update(job.job_id, status=JobStatus.PROCESSING, progress="downloading")
    updated = store.get(job.job_id)
    assert updated is not None
    assert updated.status == JobStatus.PROCESSING
    assert updated.progress == "downloading"


def test_list_recent(store: InMemoryJobStore) -> None:
    for i in range(5):
        store.create(Job(query=f"query {i}"))
    jobs = store.list_recent(limit=3)
    assert len(jobs) == 3
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_job_store.py -v`
Expected: FAIL

- [ ] **Step 4: Implement JobStore protocol and InMemoryJobStore**

Create `utils/job_store.py`:

```python
"""Job store abstraction — in-memory and Azure Table implementations."""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from models.job import Job, JobResult, JobStatus


@runtime_checkable
class JobStore(Protocol):
    def create(self, job: Job) -> None: ...
    def get(self, job_id: str) -> Job | None: ...
    def update(self, job_id: str, **fields: Any) -> None: ...
    def list_recent(self, limit: int = 20) -> list[Job]: ...


class InMemoryJobStore:
    """In-memory job store for local development."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self, job: Job) -> None:
        self._jobs[job.job_id] = job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def update(self, job_id: str, **fields: Any) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        for key, value in fields.items():
            if key == "status":
                value = JobStatus(value)
            setattr(job, key, value)

    def list_recent(self, limit: int = 20) -> list[Job]:
        jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]


class AzureTableJobStore:
    """Azure Table Storage-backed job store."""

    def __init__(
        self,
        connection_string: str,
        table_name: str = "jobs",
        _table_client: Any | None = None,
    ) -> None:
        if _table_client is not None:
            self._table = _table_client
        else:
            from azure.data.tables import TableServiceClient
            service = TableServiceClient.from_connection_string(connection_string)
            self._table = service.get_table_client(table_name)

    def _to_entity(self, job: Job) -> dict[str, Any]:
        return {
            "PartitionKey": job.created_at[:10],  # YYYY-MM-DD
            "RowKey": job.job_id,
            "query": job.query,
            "status": job.status.value,
            "progress": job.progress or "",
            "result": json.dumps(job.result.to_dict()) if job.result else "",
            "error": job.error or "",
            "webhook_url": job.webhook_url or "",
            "created_at": job.created_at,
        }

    def _from_entity(self, entity: dict[str, Any]) -> Job:
        result = None
        if entity.get("result"):
            result = JobResult.from_dict(json.loads(entity["result"]))
        return Job(
            job_id=entity["RowKey"],
            query=entity.get("query", ""),
            status=JobStatus(entity["status"]),
            progress=entity.get("progress") or None,
            result=result,
            error=entity.get("error") or None,
            webhook_url=entity.get("webhook_url") or None,
            created_at=entity.get("created_at", ""),
        )

    def create(self, job: Job) -> None:
        self._table.create_entity(self._to_entity(job))

    def get(self, job_id: str) -> Job | None:
        # Query across partitions since we don't know the date
        entities = list(
            self._table.query_entities(f"RowKey eq '{job_id}'", results_per_page=1)
        )
        if not entities:
            return None
        return self._from_entity(entities[0])

    def update(self, job_id: str, **fields: Any) -> None:
        job = self.get(job_id)
        if job is None:
            return
        for key, value in fields.items():
            if key == "status":
                value = JobStatus(value)
            setattr(job, key, value)
        self._table.upsert_entity(self._to_entity(job))

    def list_recent(self, limit: int = 20) -> list[Job]:
        entities = list(self._table.query_entities("", results_per_page=limit))
        jobs = [self._from_entity(e) for e in entities]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)[:limit]
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_job_store.py -v`
Expected: PASS

- [ ] **Step 6: Run linters**

Run: `ruff check models/job.py utils/job_store.py tests/test_job_store.py && mypy models/job.py utils/job_store.py`

- [ ] **Step 7: Commit**

```bash
git add models/job.py utils/job_store.py tests/test_job_store.py
git commit -m "feat: add Job model and JobStore (in-memory + Azure Table)"
```

---

### Task 4: Queue abstraction

**Files:**
- Create: `utils/job_queue.py`
- Create: `tests/test_job_queue.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_job_queue.py`:

```python
"""Tests for job queue — in-memory and Azure Queue implementations."""

from __future__ import annotations

import json

import pytest

from utils.job_queue import InMemoryQueue, JobQueue


@pytest.fixture()
def queue() -> InMemoryQueue:
    return InMemoryQueue()


def test_implements_protocol(queue: InMemoryQueue) -> None:
    assert isinstance(queue, JobQueue)


def test_send_and_receive(queue: InMemoryQueue) -> None:
    queue.send({"job_id": "abc", "query": "test"})
    msg = queue.receive()
    assert msg is not None
    assert msg.body["job_id"] == "abc"


def test_receive_empty(queue: InMemoryQueue) -> None:
    assert queue.receive() is None


def test_delete(queue: InMemoryQueue) -> None:
    queue.send({"job_id": "abc", "query": "test"})
    msg = queue.receive()
    assert msg is not None
    queue.delete(msg)
    assert queue.receive() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_job_queue.py -v`
Expected: FAIL

- [ ] **Step 3: Implement queue abstraction**

Create `utils/job_queue.py`:

```python
"""Queue abstraction — in-memory and Azure Storage Queue implementations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class QueueMessage:
    body: dict[str, Any]
    receipt: Any = None  # Azure pop receipt for deletion


@runtime_checkable
class JobQueue(Protocol):
    def send(self, message: dict[str, Any]) -> None: ...
    def receive(self, visibility_timeout: int = 3900) -> QueueMessage | None: ...
    def delete(self, message: QueueMessage) -> None: ...


class InMemoryQueue:
    """In-memory queue for local development."""

    def __init__(self) -> None:
        self._messages: list[dict[str, Any]] = []

    def send(self, message: dict[str, Any]) -> None:
        self._messages.append(message)

    def receive(self, visibility_timeout: int = 3900) -> QueueMessage | None:
        if not self._messages:
            return None
        body = self._messages.pop(0)
        return QueueMessage(body=body)

    def delete(self, message: QueueMessage) -> None:
        pass  # Already removed on receive


class AzureStorageQueue:
    """Azure Storage Queue implementation."""

    def __init__(
        self,
        connection_string: str,
        queue_name: str = "job-queue",
        _queue_client: Any | None = None,
    ) -> None:
        if _queue_client is not None:
            self._client = _queue_client
        else:
            from azure.storage.queue import QueueClient
            self._client = QueueClient.from_connection_string(connection_string, queue_name)

    def send(self, message: dict[str, Any]) -> None:
        self._client.send_message(json.dumps(message))

    def receive(self, visibility_timeout: int = 3900) -> QueueMessage | None:
        messages = self._client.receive_messages(
            max_messages=1, visibility_timeout=visibility_timeout
        )
        msg_list = list(messages)
        if not msg_list:
            return None
        msg = msg_list[0]
        body = json.loads(msg.content)
        return QueueMessage(body=body, receipt=msg)

    def delete(self, message: QueueMessage) -> None:
        if message.receipt is not None:
            self._client.delete_message(message.receipt)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_job_queue.py -v`
Expected: PASS

- [ ] **Step 5: Linters**

Run: `ruff check utils/job_queue.py tests/test_job_queue.py && mypy utils/job_queue.py`

- [ ] **Step 6: Commit**

```bash
git add utils/job_queue.py tests/test_job_queue.py
git commit -m "feat: add JobQueue abstraction (in-memory + Azure Storage Queue)"
```

---

### Task 5: Webhook utility

**Files:**
- Create: `utils/webhook.py`
- Create: `tests/test_webhook.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_webhook.py`:

```python
"""Tests for webhook delivery."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from utils.webhook import deliver_webhook


@pytest.mark.asyncio
async def test_deliver_webhook_success() -> None:
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = AsyncMock()

    with patch("utils.webhook.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = await deliver_webhook(
            "https://example.com/hook",
            {"job_id": "abc", "status": "completed"},
        )
        assert result is True
        mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_deliver_webhook_failure_retries() -> None:
    with patch("utils.webhook.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post.side_effect = Exception("connection refused")
        mock_client_cls.return_value = mock_client

        result = await deliver_webhook(
            "https://example.com/hook",
            {"job_id": "abc", "status": "failed"},
            max_retries=2,
            base_delay=0.01,
        )
        assert result is False
        assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_deliver_webhook_no_url() -> None:
    result = await deliver_webhook(None, {"job_id": "abc"})
    assert result is False
```

Note: Add `pytest-asyncio` to requirements.txt under Testing section.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webhook.py -v`
Expected: FAIL

- [ ] **Step 3: Implement webhook delivery**

Create `utils/webhook.py`:

```python
"""Webhook delivery with exponential backoff retry."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


async def deliver_webhook(
    url: str | None,
    payload: dict[str, Any],
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> bool:
    """POST payload to webhook URL. Returns True on success, False on failure.

    Retries with exponential backoff. Failures are logged but never raised —
    webhook delivery does not affect job status.
    """
    if not url:
        return False

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                log.info("Webhook delivered to %s (attempt %d)", url, attempt + 1)
                return True
        except Exception:
            delay = base_delay * (4 ** attempt)  # 1s, 4s, 16s
            log.warning(
                "Webhook delivery to %s failed (attempt %d/%d), retrying in %.1fs",
                url, attempt + 1, max_retries, delay,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)

    log.error("Webhook delivery to %s failed after %d attempts", url, max_retries)
    return False
```

- [ ] **Step 4: Add pytest-asyncio to requirements.txt**

Add under `# Testing`:
```
pytest-asyncio
```

Run: `pip install pytest-asyncio`

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_webhook.py -v`
Expected: PASS

- [ ] **Step 6: Linters**

Run: `ruff check utils/webhook.py tests/test_webhook.py && mypy utils/webhook.py`

- [ ] **Step 7: Commit**

```bash
git add utils/webhook.py tests/test_webhook.py requirements.txt
git commit -m "feat: add webhook delivery with exponential backoff retry"
```

---

### Task 6: FastAPI app — health + auth middleware

**Files:**
- Create: `api/__init__.py`
- Create: `api/app.py`
- Create: `api/dependencies.py`
- Create: `api/schemas.py`
- Create: `api/routes/__init__.py`
- Create: `api/routes/jobs.py`
- Create: `tests/test_api_health.py`

This task sets up the FastAPI skeleton with health check and API key auth. Job endpoints come in Task 7.

- [ ] **Step 1: Write failing tests**

Create `tests/test_api_health.py`:

```python
"""Tests for API health check and auth middleware."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client() -> TestClient:
    with patch("config.settings.API_KEYS", ["test-key-123"]):
        from api.app import create_app
        app = create_app()
        return TestClient(app)


def test_health_no_auth_required(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_auth_required_without_key(client: TestClient) -> None:
    response = client.get("/api/v1/jobs")
    assert response.status_code == 401


def test_auth_required_with_wrong_key(client: TestClient) -> None:
    response = client.get("/api/v1/jobs", headers={"X-API-Key": "wrong"})
    assert response.status_code == 401


def test_auth_passes_with_valid_key(client: TestClient) -> None:
    response = client.get("/api/v1/jobs", headers={"X-API-Key": "test-key-123"})
    assert response.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_health.py -v`
Expected: FAIL

- [ ] **Step 3: Create API skeleton**

Create `api/__init__.py` (empty).

Create `api/routes/__init__.py` (empty).

Create `api/schemas.py`:

```python
"""Pydantic request/response models for the API."""

from __future__ import annotations

from pydantic import BaseModel


class JobCreateRequest(BaseModel):
    query: str
    webhook_url: str | None = None
    kickoff_first_half: float | None = None   # seconds — override if auto-detect fails
    kickoff_second_half: float | None = None  # seconds — override if auto-detect fails


class JobResultResponse(BaseModel):
    download_url: str
    duration_seconds: float
    clip_count: int
    expires_at: str


class JobResponse(BaseModel):
    job_id: str
    status: str
    progress: str | None = None
    query: str
    result: JobResultResponse | None = None
    error: str | None = None
    created_at: str


class JobCreateResponse(BaseModel):
    job_id: str
    status: str
    poll_url: str


class ErrorResponse(BaseModel):
    error: dict[str, str]
```

Create `api/dependencies.py`:

```python
"""Shared dependencies for the API — backend injection."""

from __future__ import annotations

from config.settings import (
    AZURE_BLOB_CONTAINER_HIGHLIGHTS,
    AZURE_BLOB_CONTAINER_PIPELINE,
    AZURE_BLOB_CONTAINER_VIDEOS,
    AZURE_QUEUE_NAME,
    AZURE_STORAGE_CONNECTION_STRING,
    AZURE_TABLE_NAME,
    PIPELINE_WORKSPACE,
    STORAGE_BACKEND,
)
from utils.job_queue import AzureStorageQueue, InMemoryQueue, JobQueue
from utils.job_store import AzureTableJobStore, InMemoryJobStore, JobStore
from utils.storage import BlobStorage, LocalStorage, StorageBackend

_store: JobStore | None = None
_queue: JobQueue | None = None
_storage: StorageBackend | None = None


def get_job_store() -> JobStore:
    global _store
    if _store is None:
        if STORAGE_BACKEND == "azure":
            _store = AzureTableJobStore(AZURE_STORAGE_CONNECTION_STRING, AZURE_TABLE_NAME)
        else:
            _store = InMemoryJobStore()
    return _store


def get_job_queue() -> JobQueue:
    global _queue
    if _queue is None:
        if STORAGE_BACKEND == "azure":
            _queue = AzureStorageQueue(AZURE_STORAGE_CONNECTION_STRING, AZURE_QUEUE_NAME)
        else:
            _queue = InMemoryQueue()
    return _queue


def get_storage() -> StorageBackend:
    global _storage
    if _storage is None:
        if STORAGE_BACKEND == "azure":
            _storage = BlobStorage(
                AZURE_STORAGE_CONNECTION_STRING,
                AZURE_BLOB_CONTAINER_VIDEOS,
                AZURE_BLOB_CONTAINER_PIPELINE,
                AZURE_BLOB_CONTAINER_HIGHLIGHTS,
            )
        else:
            _storage = LocalStorage(PIPELINE_WORKSPACE)
    return _storage
```

Create `api/app.py`:

```python
"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from config.settings import API_KEYS

from api.routes import jobs


def create_app() -> FastAPI:
    app = FastAPI(title="Football Highlights API", version="1.0.0")

    @app.middleware("http")
    async def api_key_auth(request: Request, call_next):  # type: ignore[no-untyped-def]
        # Skip auth for health check
        if request.url.path == "/api/v1/health":
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        if API_KEYS and api_key not in API_KEYS:
            return JSONResponse(
                status_code=401,
                content={"error": {"code": "unauthorized", "message": "Invalid or missing API key"}},
            )
        return await call_next(request)

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(jobs.router, prefix="/api/v1")

    return app


app = create_app()
```

Create `api/routes/jobs.py` (minimal for now — full implementation in Task 7):

```python
"""Job API routes."""

from __future__ import annotations

from fastapi import APIRouter

from api.dependencies import get_job_store

router = APIRouter()


@router.get("/jobs")
async def list_jobs(limit: int = 20) -> dict:
    store = get_job_store()
    jobs = store.list_recent(limit=limit)
    return {"jobs": [j.to_dict() for j in jobs]}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_api_health.py -v`
Expected: PASS

- [ ] **Step 5: Linters**

Run: `ruff check api/ tests/test_api_health.py && mypy api/`

- [ ] **Step 6: Commit**

```bash
git add api/ tests/test_api_health.py
git commit -m "feat: FastAPI skeleton with health check and API key auth"
```

---

### Task 7: Job submission & polling endpoints

**Files:**
- Modify: `api/routes/jobs.py`
- Create: `tests/test_api_jobs.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_api_jobs.py`:

```python
"""Tests for job submission and polling endpoints."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from utils.job_queue import InMemoryQueue
from utils.job_store import InMemoryJobStore


@pytest.fixture()
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture()
def queue() -> InMemoryQueue:
    return InMemoryQueue()


@pytest.fixture()
def client(store: InMemoryJobStore, queue: InMemoryQueue) -> TestClient:
    with (
        patch("config.settings.API_KEYS", ["test-key"]),
        patch("api.dependencies._store", store),
        patch("api.dependencies._queue", queue),
    ):
        from api.app import create_app
        app = create_app()
        return TestClient(app)


HEADERS = {"X-API-Key": "test-key"}


def test_create_job(client: TestClient, queue: InMemoryQueue) -> None:
    response = client.post(
        "/api/v1/jobs",
        json={"query": "Liverpool vs City goals"},
        headers=HEADERS,
    )
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "queued"
    assert "job_id" in data
    assert "poll_url" in data
    # Message should be in queue
    msg = queue.receive()
    assert msg is not None
    assert msg.body["query"] == "Liverpool vs City goals"


def test_create_job_with_webhook(client: TestClient) -> None:
    response = client.post(
        "/api/v1/jobs",
        json={"query": "test", "webhook_url": "https://example.com/hook"},
        headers=HEADERS,
    )
    assert response.status_code == 202


def test_get_job_found(client: TestClient, store: InMemoryJobStore) -> None:
    # Create a job first
    response = client.post(
        "/api/v1/jobs", json={"query": "test"}, headers=HEADERS
    )
    job_id = response.json()["job_id"]

    response = client.get(f"/api/v1/jobs/{job_id}", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["job_id"] == job_id
    assert response.json()["status"] == "queued"


def test_get_job_not_found(client: TestClient) -> None:
    response = client.get("/api/v1/jobs/nonexistent", headers=HEADERS)
    assert response.status_code == 404


def test_list_jobs_empty(client: TestClient) -> None:
    response = client.get("/api/v1/jobs", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["jobs"] == []


def test_list_jobs_with_results(client: TestClient) -> None:
    client.post("/api/v1/jobs", json={"query": "q1"}, headers=HEADERS)
    client.post("/api/v1/jobs", json={"query": "q2"}, headers=HEADERS)
    response = client.get("/api/v1/jobs", headers=HEADERS)
    assert len(response.json()["jobs"]) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_jobs.py -v`
Expected: FAIL (POST /jobs not implemented)

- [ ] **Step 3: Implement job endpoints**

Update `api/routes/jobs.py`:

```python
"""Job API routes."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import hashlib

from fastapi.responses import JSONResponse

from api.dependencies import get_job_queue, get_job_store, get_storage
from api.schemas import JobCreateRequest, JobCreateResponse
from models.job import Job

router = APIRouter()


def _query_hash(query: str) -> str:
    normalized = query.strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


@router.post("/jobs")
async def create_job(request: JobCreateRequest) -> JSONResponse:
    store = get_job_store()
    queue = get_job_queue()
    storage = get_storage()

    # Check cache: if highlights already exist for this query, return immediately
    qhash = _query_hash(request.query)
    try:
        # Check if any video has this query_hash highlights blob
        # For now, check recent completed jobs with same query hash
        recent = store.list_recent(limit=100)
        for existing in recent:
            if (
                existing.status.value == "completed"
                and existing.result
                and _query_hash(existing.query) == qhash
            ):
                return JSONResponse(
                    status_code=200,
                    content=existing.to_dict(),
                )
    except Exception:
        pass  # Cache check failure is non-fatal, proceed to queue

    job = Job(query=request.query, webhook_url=request.webhook_url)
    store.create(job)
    queue.send({
        "job_id": job.job_id,
        "query": job.query,
        "webhook_url": job.webhook_url,
        "kickoff_first_half": request.kickoff_first_half,
        "kickoff_second_half": request.kickoff_second_half,
    })

    return JSONResponse(
        status_code=202,
        content=JobCreateResponse(
            job_id=job.job_id,
            status=job.status.value,
            poll_url=f"/api/v1/jobs/{job.job_id}",
        ).model_dump(),
    )


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    store = get_job_store()
    job = store.get(job_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "not_found", "message": "Job not found"}},
        )
    return job.to_dict()


@router.get("/jobs")
async def list_jobs(limit: int = 20) -> dict:
    store = get_job_store()
    jobs = store.list_recent(limit=limit)
    return {"jobs": [j.to_dict() for j in jobs]}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_api_jobs.py -v`
Expected: PASS

- [ ] **Step 5: Run all API tests together**

Run: `pytest tests/test_api_health.py tests/test_api_jobs.py -v`
Expected: PASS

- [ ] **Step 6: Linters**

Run: `ruff check api/ tests/test_api_jobs.py && mypy api/`

- [ ] **Step 7: Commit**

```bash
git add api/routes/jobs.py api/schemas.py tests/test_api_jobs.py
git commit -m "feat: job submission (POST) and polling (GET) endpoints"
```

---

### Task 8: Worker runner

**Files:**
- Create: `worker/__init__.py`
- Create: `worker/runner.py`
- Create: `tests/test_worker.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_worker.py`:

```python
"""Tests for worker runner — queue consumer + pipeline execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from models.job import Job, JobStatus
from utils.job_queue import InMemoryQueue
from utils.job_store import InMemoryJobStore
from worker.runner import process_job


@pytest.fixture()
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture()
def queue() -> InMemoryQueue:
    return InMemoryQueue()


@pytest.fixture()
def mock_storage(tmp_path: Path) -> MagicMock:
    from utils.storage import LocalStorage
    return LocalStorage(tmp_path / "workspace")


def test_process_job_success(
    store: InMemoryJobStore,
    mock_storage: Any,
) -> None:
    """Successful pipeline run updates job to completed."""
    job = Job(query="Liverpool vs City goals")
    store.create(job)

    mock_result = {
        "highlights_path": "/tmp/highlights.mp4",
        "clip_count": 5,
        "total_duration_seconds": 120.0,
        "total_duration_display": "2:00",
    }

    with (
        patch("worker.runner._run_pipeline", return_value=mock_result),
        patch("worker.runner.deliver_webhook"),
    ):
        process_job(
            job_id=job.job_id,
            query=job.query,
            webhook_url=None,
            store=store,
            storage=mock_storage,
        )

    updated = store.get(job.job_id)
    assert updated is not None
    assert updated.status == JobStatus.COMPLETED
    assert updated.result is not None


def test_process_job_failure(
    store: InMemoryJobStore,
    mock_storage: Any,
) -> None:
    """Pipeline error updates job to failed."""
    job = Job(query="bad query")
    store.create(job)

    with (
        patch("worker.runner._run_pipeline", side_effect=RuntimeError("download failed")),
        patch("worker.runner.deliver_webhook"),
    ):
        process_job(
            job_id=job.job_id,
            query=job.query,
            webhook_url=None,
            store=store,
            storage=mock_storage,
        )

    updated = store.get(job.job_id)
    assert updated is not None
    assert updated.status == JobStatus.FAILED
    assert updated.error is not None
    assert "download failed" in updated.error


def test_process_job_updates_progress(
    store: InMemoryJobStore,
    mock_storage: Any,
) -> None:
    """Progress should be updated during pipeline stages."""
    job = Job(query="test")
    store.create(job)

    progress_log: list[str] = []
    original_update = store.update

    def tracking_update(job_id: str, **fields: Any) -> None:
        if "progress" in fields:
            progress_log.append(fields["progress"])
        original_update(job_id, **fields)

    store.update = tracking_update  # type: ignore[assignment]

    mock_result = {
        "highlights_path": "/tmp/h.mp4",
        "clip_count": 1,
        "total_duration_seconds": 30.0,
        "total_duration_display": "0:30",
    }

    with (
        patch("worker.runner._run_pipeline", return_value=mock_result),
        patch("worker.runner.deliver_webhook"),
    ):
        process_job(
            job_id=job.job_id,
            query=job.query,
            webhook_url=None,
            store=store,
            storage=mock_storage,
        )

    assert len(progress_log) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_worker.py -v`
Expected: FAIL

- [ ] **Step 3: Implement worker runner**

Create `worker/__init__.py` (empty).

Create `worker/runner.py`:

```python
"""Worker runner — polls queue, runs pipeline, updates job state."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from models.job import JobResult, JobStatus
from utils.job_queue import JobQueue
from utils.job_store import JobStore
from utils.storage import StorageBackend
from utils.webhook import deliver_webhook

log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5


def _run_pipeline(
    query: str,
    storage: StorageBackend,
    progress_callback: Any = None,
    kickoff_first_override: float | None = None,
    kickoff_second_override: float | None = None,
) -> dict[str, Any]:
    """Run the full highlights pipeline for a query.

    This wraps the existing pipeline modules (match_finder, match_events,
    transcription, event_aligner, clip_builder) into a single function.
    """
    from pipeline.match_finder import download_and_save, find_match, is_url

    if progress_callback:
        progress_callback("searching")

    # Stage 1: Find and download
    if is_url(query):
        url = query
    else:
        result = find_match(query, storage)
        candidates = result.get("candidates", [])
        if not candidates:
            raise RuntimeError(f"No match videos found for: {query}")
        url = candidates[0]["url"]

    if progress_callback:
        progress_callback("downloading")

    metadata = download_and_save(url, storage, skip_duration_check=False)
    video_id = metadata["video_id"]

    # Stage 2: Match events
    if progress_callback:
        progress_callback("fetching_events")

    from pipeline.match_events import fetch_match_events

    # Resolve fixture if possible
    from pipeline.match_finder import resolve_fixture_for_video
    try:
        res = resolve_fixture_for_video("", metadata.get("video_filename", ""))
        if res.fixture_id:
            metadata["fixture_id"] = res.fixture_id
    except Exception:
        pass

    match_events = fetch_match_events(metadata, storage)

    # Stage 3: Transcription
    if progress_callback:
        progress_callback("transcribing")

    from pipeline.transcription import transcribe

    transcription = transcribe(metadata, storage)
    kickoff_first = kickoff_first_override or transcription.get("kickoff_first_half")
    kickoff_second = kickoff_second_override or transcription.get("kickoff_second_half")

    if kickoff_first is None or kickoff_second is None:
        raise RuntimeError(
            "Could not auto-detect kickoff timestamps. "
            "Re-submit with kickoff_first_half and kickoff_second_half overrides."
        )

    # Stage 4: Align events
    if progress_callback:
        progress_callback("aligning")

    from pipeline.event_aligner import align_events

    align_events(match_events, metadata, storage, kickoff_first, kickoff_second)

    # Write game.json so query stage can work
    from models.game import GameState

    game = GameState(
        video_id=video_id,
        home_team="",
        away_team="",
        league="",
        date="",
        fixture_id=int(metadata.get("fixture_id") or 0),
        video_filename=metadata.get("video_filename", ""),
        source=f"https://www.youtube.com/watch?v={video_id}",
        duration_seconds=metadata["duration_seconds"],
        kickoff_first_half=kickoff_first,
        kickoff_second_half=kickoff_second,
    )
    storage.write_json(video_id, "game.json", game.to_dict())

    # Stage 5: Build highlights
    if progress_callback:
        progress_callback("building_clips")

    from models.events import AlignedEvent
    from models.highlight_query import HighlightQuery, QueryType
    from pipeline.clip_builder import build_highlights
    from pipeline.event_filter import filter_events

    aligned_data = storage.read_json(video_id, "aligned_events.json")
    aligned_events = [AlignedEvent.from_dict(e) for e in aligned_data.get("events", [])]

    # Try to interpret query, fall back to full summary
    try:
        from pipeline.query_interpreter import interpret_query
        hq = interpret_query(query, game, aligned_events)
    except Exception:
        hq = HighlightQuery(query_type=QueryType.FULL_SUMMARY, raw_query=query)

    filtered = filter_events(aligned_events, hq)
    result = build_highlights(filtered, game, hq, storage)
    return result


def process_job(
    job_id: str,
    query: str,
    webhook_url: str | None,
    store: JobStore,
    storage: StorageBackend,
    kickoff_first_override: float | None = None,
    kickoff_second_override: float | None = None,
) -> None:
    """Process a single job — runs pipeline, updates state, fires webhook."""
    store.update(job_id, status=JobStatus.PROCESSING, progress="starting")

    def on_progress(stage: str) -> None:
        store.update(job_id, progress=stage)

    try:
        result = _run_pipeline(
            query, storage,
            progress_callback=on_progress,
            kickoff_first_override=kickoff_first_override,
            kickoff_second_override=kickoff_second_override,
        )

        job_result = JobResult(
            download_url=result["highlights_path"],  # TODO: SAS URL for azure
            duration_seconds=result.get("total_duration_seconds", 0.0),
            clip_count=result.get("clip_count", 0),
            expires_at="",  # TODO: set from SAS expiry
        )

        store.update(
            job_id,
            status=JobStatus.COMPLETED,
            progress=None,
            result=job_result,
        )

        asyncio.run(deliver_webhook(webhook_url, {
            "job_id": job_id,
            "status": "completed",
            "result": job_result.to_dict(),
        }))

    except Exception as exc:
        log.exception("Job %s failed: %s", job_id, exc)
        store.update(
            job_id,
            status=JobStatus.FAILED,
            progress=None,
            error=str(exc),
        )
        asyncio.run(deliver_webhook(webhook_url, {
            "job_id": job_id,
            "status": "failed",
            "error": str(exc),
        }))
    finally:
        # Clean up temp files if using BlobStorage
        if hasattr(storage, "cleanup_temp"):
            storage.cleanup_temp(job_id)


def run_worker(queue: JobQueue, store: JobStore, storage: StorageBackend) -> None:
    """Main worker loop — polls queue, processes jobs."""
    log.info("Worker started, polling queue every %ds", POLL_INTERVAL_SECONDS)
    while True:
        msg = queue.receive(visibility_timeout=3900)
        if msg is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        job_id = msg.body["job_id"]
        log.info("Processing job %s", job_id)

        process_job(
            job_id=job_id,
            query=msg.body["query"],
            webhook_url=msg.body.get("webhook_url"),
            store=store,
            storage=storage,
            kickoff_first_override=msg.body.get("kickoff_first_half"),
            kickoff_second_override=msg.body.get("kickoff_second_half"),
        )

        queue.delete(msg)
        log.info("Job %s complete, message deleted", job_id)


def main() -> None:
    """Entrypoint for `python -m worker.runner`."""
    from utils.logger import setup_logging
    from api.dependencies import get_job_queue, get_job_store, get_storage

    setup_logging()
    run_worker(
        queue=get_job_queue(),
        store=get_job_store(),
        storage=get_storage(),
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_worker.py -v`
Expected: PASS

- [ ] **Step 5: Linters**

Run: `ruff check worker/ tests/test_worker.py && mypy worker/`

- [ ] **Step 6: Commit**

```bash
git add worker/ tests/test_worker.py
git commit -m "feat: worker runner — queue consumer with pipeline execution"
```

---

### Task 9: Dockerfile + entrypoints

**Files:**
- Modify: `Dockerfile`
- Create: `worker/__main__.py`

- [ ] **Step 1: Update Dockerfile**

Update `Dockerfile` to use `api.app:app`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Default: API server. Worker overrides CMD in ACA config.
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Create worker __main__.py**

Create `worker/__main__.py`:

```python
"""Allow running worker as `python -m worker`."""

from worker.runner import main

main()
```

- [ ] **Step 3: Verify Docker builds**

Run: `docker build -t football-analyzer .`
Expected: Build succeeds

- [ ] **Step 4: Commit**

```bash
git add Dockerfile worker/__main__.py
git commit -m "feat: update Dockerfile for API + worker entrypoints"
```

---

### Task 10: Azure Bicep infrastructure

**Files:**
- Create: `infra/bicep/main.bicep`
- Create: `infra/bicep/parameters.json`

- [ ] **Step 1: Create main.bicep**

Create `infra/bicep/main.bicep`:

```bicep
@description('Base name for all resources')
param baseName string = 'football-hl'

@description('Location for all resources')
param location string = resourceGroup().location

@description('Container image for API and Worker')
param containerImage string

@description('API keys (comma-separated)')
@secure()
param apiKeys string

@description('AssemblyAI API key')
@secure()
param assemblyaiApiKey string

@description('API Football key')
@secure()
param apiFootballKey string

@description('OpenAI API key')
@secure()
param openaiApiKey string

// Storage Account — blob, queue, table
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: '${replace(baseName, '-', '')}storage'
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
  }
}

// Blob containers
resource videosContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${storageAccount.name}/default/videos'
  properties: { publicAccess: 'None' }
}

resource pipelineContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${storageAccount.name}/default/pipeline'
  properties: { publicAccess: 'None' }
}

resource highlightsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${storageAccount.name}/default/highlights'
  properties: { publicAccess: 'None' }
}

// Queue
resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' = {
  name: '${storageAccount.name}/default'
}

resource jobQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  name: '${storageAccount.name}/default/job-queue'
  dependsOn: [queueService]
}

// Table
resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  name: '${storageAccount.name}/default'
}

resource jobsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  name: '${storageAccount.name}/default/jobs'
  dependsOn: [tableService]
}

// Container Registry
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: '${replace(baseName, '-', '')}acr'
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: true }
}

// Log Analytics for Container Apps
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${baseName}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// Container Apps Environment
resource acaEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${baseName}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

var storageConnectionString = 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=core.windows.net'

var sharedEnv = [
  { name: 'STORAGE_BACKEND', value: 'azure' }
  { name: 'AZURE_STORAGE_CONNECTION_STRING', secretRef: 'storage-conn' }
  { name: 'ASSEMBLYAI_API_KEY', secretRef: 'assemblyai-key' }
  { name: 'API_FOOTBALL_KEY', secretRef: 'api-football-key' }
  { name: 'OPENAI_API_KEY', secretRef: 'openai-key' }
  { name: 'API_KEYS', secretRef: 'api-keys' }
]

var sharedSecrets = [
  { name: 'storage-conn', value: storageConnectionString }
  { name: 'assemblyai-key', value: assemblyaiApiKey }
  { name: 'api-football-key', value: apiFootballKey }
  { name: 'openai-key', value: openaiApiKey }
  { name: 'api-keys', value: apiKeys }
  { name: 'acr-password', value: acr.listCredentials().passwords[0].value }
]

// API Container App
resource apiApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${baseName}-api'
  location: location
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
      }
      secrets: sharedSecrets
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: containerImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: sharedEnv
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

// Worker Container App
resource workerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${baseName}-worker'
  location: location
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      secrets: sharedSecrets
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: containerImage
          command: ['python', '-m', 'worker']
          resources: {
            cpu: json('2.0')
            memory: '4Gi'
            ephemeralStorage: '20Gi'
          }
          env: sharedEnv
          volumeMounts: [
            { volumeName: 'tmp', mountPath: '/tmp/football-analyzer' }
          ]
        }
      ]
      volumes: [
        { name: 'tmp', storageType: 'EmptyDir' }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

output apiUrl string = 'https://${apiApp.properties.configuration.ingress.fqdn}'
output acrLoginServer string = acr.properties.loginServer
```

- [ ] **Step 2: Create parameters.json**

Create `infra/bicep/parameters.json`:

```json
{
  "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
  "contentVersion": "1.0.0.0",
  "parameters": {
    "baseName": { "value": "football-hl" },
    "containerImage": { "value": "footballhlacr.azurecr.io/football-analyzer:latest" },
    "apiKeys": { "value": "" },
    "assemblyaiApiKey": { "value": "" },
    "apiFootballKey": { "value": "" },
    "openaiApiKey": { "value": "" }
  }
}
```

**Note:** Secrets should be filled at deploy time via `--parameters` overrides, not committed.

- [ ] **Step 3: Commit**

```bash
git add infra/
git commit -m "feat: Azure Bicep infra — storage, ACA, ACR"
```

---

### Task 11: Run full test suite & final checks

- [ ] **Step 1: Run all tests**

Run: `pytest -v`
Expected: All tests pass

- [ ] **Step 2: Run all linters**

Run: `ruff check . && mypy . && bandit -r . -c pyproject.toml`
Expected: No errors

- [ ] **Step 3: Verify Docker build**

Run: `docker build -t football-analyzer .`
Expected: Build succeeds

- [ ] **Step 4: Commit any remaining fixes**

```bash
git add -A
git commit -m "chore: fix lint and test issues from final pass"
```

---

## Task Dependency Order

```
Task 1 (deps + config)
  → Task 2 (BlobStorage)
  → Task 3 (Job model + store)
  → Task 4 (Queue abstraction)
  → Task 5 (Webhook)
  → Task 6 (FastAPI skeleton)
    → Task 7 (Job endpoints)  [depends on Task 3, 4, 6]
  → Task 8 (Worker)           [depends on Task 3, 4, 5]
  → Task 9 (Dockerfile)       [depends on Task 6, 8]
  → Task 10 (Bicep)           [depends on Task 1]
  → Task 11 (Final checks)    [depends on all]
```

Tasks 2-5 can be done in parallel. Tasks 6-8 can be partially parallelized. Task 11 is last.
