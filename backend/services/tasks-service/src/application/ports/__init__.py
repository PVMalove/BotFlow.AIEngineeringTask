"""Boundaries of the task use cases."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from domain.tasks import Task, TaskStatus


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


class Unauthenticated(Exception):
    """No valid session."""


class NotApproved(Exception):
    """The session's member is not an approved participant."""


class IdentityUnavailable(Exception):
    """Identity cannot confirm the session; access is never assumed."""


class InvalidCursor(Exception):
    """The page cursor is not one this service issued."""


class TaskNotFound(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PageCursor:
    """Position after the last row of a page: ordering is (created_at, id) DESC."""

    created_at: datetime
    id: UUID


class MemberAuthenticator(Protocol):
    async def require_approved(self, token: str) -> None:
        """Raise Unauthenticated, NotApproved or IdentityUnavailable."""
        ...


class TaskReader(Protocol):
    async def list_page(
        self, status: TaskStatus | None, after: PageCursor | None, limit: int
    ) -> list[Task]:
        """Newest first, strictly after `after`."""
        ...

    async def get(self, task_id: UUID) -> Task | None: ...
