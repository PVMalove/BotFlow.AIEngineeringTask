"""Boundaries of the task use cases."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from kernel_domain.result import Result
from kernel_platform.pagination import Cursor
from kernel_platform.security import Actor

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


class IdentityUnavailable(Exception):
    """Identity cannot confirm the session; access is never assumed."""


class MemberAuthenticator(Protocol):
    async def require_approved(self, token: str) -> Result[Actor]:
        """Unauthenticated / not approved come back as errors; raises
        IdentityUnavailable when Identity cannot answer."""
        ...


class TaskReader(Protocol):
    async def list_page(
        self, status: TaskStatus | None, after: Cursor | None, limit: int
    ) -> list[Task]:
        """Newest first, strictly after `after`."""
        ...

    async def get(self, task_id: UUID) -> Task | None: ...


class TaskStatusStore(Protocol):
    async def change(
        self, task_id: UUID, target: TaskStatus, expected_version: int
    ) -> Result[Task]:
        """Compare the version and update in one atomic step.

        The task as stored afterwards; a not-found or version-conflict error
        when there is no such task or `expected_version` is not the current one.
        """
        ...


class BoardSignal(Protocol):
    async def changed(self, task_id: UUID) -> None:
        """Tell subscribers to re-read the board; best effort, never raises."""
        ...
