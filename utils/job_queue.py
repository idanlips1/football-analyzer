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
