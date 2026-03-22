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
