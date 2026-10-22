"""ChangeTaskStatus command: the author or an admin moves a task between statuses."""

from dataclasses import dataclass
from uuid import UUID

from kernel_domain.result import Result
from kernel_platform.security import Actor

from application.authorization import may_change_status
from application.ports import TaskReader, TaskStatusStore
from contracts.task import TaskView
from domain.errors import TaskErrors
from domain.tasks import TaskStatus


@dataclass(frozen=True)
class ChangeTaskStatusCommand:
    task_id: UUID
    status: TaskStatus
    expected_version: int
    actor: Actor


class ChangeTaskStatusCommandHandler:
    def __init__(self, reader: TaskReader, store: TaskStatusStore) -> None:
        self._reader = reader
        self._store = store

    async def execute(self, command: ChangeTaskStatusCommand) -> Result[TaskView]:
        task = await self._reader.get(command.task_id)
        if task is None:
            return Result[TaskView].fail(TaskErrors.not_found())
        if not may_change_status(command.actor, task):
            return Result[TaskView].fail(TaskErrors.forbidden())
        # Only the store can compare the version atomically with the update.
        changed = await self._store.change(
            command.task_id, command.status, command.expected_version
        )
        if changed.is_err:
            return Result[TaskView].fail(changed.error)
        return Result[TaskView].ok(TaskView.from_domain(changed.value))
