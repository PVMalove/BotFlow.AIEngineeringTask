"""Persistence contracts of the Task aggregate."""

from typing import Protocol

from domain.entities.task import Task
from domain.value_objects.task_id import TaskId


class TaskRepository(Protocol):
    async def get(self, task_id: TaskId) -> Task | None: ...

    async def add(self, task: Task) -> bool:
        """Store a new task; False, and nothing raised, when its source has one."""
        ...

    async def update_status(self, task: Task, *, loaded_version: int) -> bool:
        """Write the changed status if the stored version is still `loaded_version`.

        The comparison and the write are one statement, so a concurrent writer can
        never slip between them; False means the task changed meanwhile.
        """
        ...


class InboxRepository(Protocol):
    async def claim(self, message_id: str) -> bool:
        """Record a consumed message; False for a redelivery."""
        ...
