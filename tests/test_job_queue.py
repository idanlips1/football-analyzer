"""Tests for job queue — in-memory and Azure Queue implementations."""

from __future__ import annotations

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
