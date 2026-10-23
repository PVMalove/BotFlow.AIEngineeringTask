"""ChangeTaskStatus command: the author or an admin moves a task between statuses."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from kernel_domain.result import Result
from kernel_platform.clock import utcnow
from kernel_platform.security.actor import Actor

from application.authorization import may_change_status
from contracts.task import TaskView
from domain.errors import TaskErrors
from domain.unit_of_work import TasksUnitOfWork
from domain.value_objects.task_id import TaskId
from domain.value_objects.task_status import TaskStatus


@dataclass(frozen=True)
class ChangeTaskStatusCommand:
    task_id: UUID
    status: TaskStatus
    expected_version: int
    actor: Actor


class ChangeTaskStatusCommandHandler:
    def __init__(
        self,
        uow_factory: Callable[[], TasksUnitOfWork],
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: ChangeTaskStatusCommand) -> Result[TaskView]:
        async with self._uow_factory() as uow:
            task = await uow.tasks.get(TaskId(command.task_id))
            if task is None:
                return Result[TaskView].fail(TaskErrors.not_found())
            if not may_change_status(command.actor, task):
                return Result[TaskView].fail(TaskErrors.forbidden())
            changed = task.change_status(
                command.status, command.expected_version, self._clock()
            )
            if changed.is_err:
                return Result[TaskView].fail(changed.error)
            if changed.value:
                # Only the repository can compare the version atomically with the write.
                if not await uow.tasks.update_status(
                    task, loaded_version=command.expected_version
                ):
                    return Result[TaskView].fail(TaskErrors.version_conflict())
                await uow.commit()
        return Result[TaskView].ok(TaskView.from_domain(task))
