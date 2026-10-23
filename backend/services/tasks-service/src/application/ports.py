"""Boundaries of the task use cases."""

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from kernel_platform.pagination import Cursor

from domain.entities.task import Task
from domain.value_objects.task_id import TaskId
from domain.value_objects.task_status import TaskStatus


class TaskReader(Protocol):
    async def list_page(
        self, status: TaskStatus | None, after: Cursor | None, limit: int
    ) -> list[Task]:
        """Newest first, strictly after `after`."""
        ...

    async def get(self, task_id: TaskId) -> Task | None: ...


class IdentityUnavailable(Exception):
    """Identity cannot confirm the session; access is never assumed."""


class BoardSubscription(Protocol):
    async def wait(self, timeout: float) -> bool:
        """True when the board changed since the last call, False after `timeout`.

        Signals that piled up in between count once. A signal is only a hint to
        re-read the board: one that was missed is not an error.
        """
        ...


class BoardFeed(Protocol):
    def subscribe(self) -> AbstractAsyncContextManager[BoardSubscription]:
        """Signals are received from the moment the context is entered."""
        ...
