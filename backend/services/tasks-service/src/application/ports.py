"""Boundaries of the task use cases."""

from typing import Protocol

from kernel_domain.result import Result
from kernel_platform.pagination import Cursor
from kernel_platform.security.actor import Actor

from domain.entities.task import Task
from domain.value_objects.task_id import TaskId
from domain.value_objects.task_status import TaskStatus


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

    async def get(self, task_id: TaskId) -> Task | None: ...


class IdentityUnavailable(Exception):
    """Identity cannot confirm the session; access is never assumed."""
