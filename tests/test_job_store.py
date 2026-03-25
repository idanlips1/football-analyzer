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
