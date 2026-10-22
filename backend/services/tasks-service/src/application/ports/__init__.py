"""Boundaries of the task use cases."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class NewTask:
    id: UUID
    source_key: str
    correlation_id: UUID  # the Automation job that asked for the task
    text: str
    author_id: UUID
    author_telegram_id: int
    author_name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TaskCreatedMessage:
    """The result stored in the outbox together with the task."""

    id: UUID
    correlation_id: UUID
    type: str
    routing_key: str
    payload: dict[str, Any]  # the complete envelope


class TaskCommandStore(Protocol):
    async def create(
        self, message_id: str, task: NewTask, created: TaskCreatedMessage
    ) -> bool:
        """Inbox record, task and result in ONE transaction.

        False for a redelivered message. The unique source makes a second message
        for the same source a no-op as well.
        """
        ...
