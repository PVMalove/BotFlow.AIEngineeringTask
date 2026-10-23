"""GetTask query: one card of the shared board."""

from dataclasses import dataclass
from uuid import UUID

from kernel_domain.result import Result

from application.ports import TaskReader
from contracts.task import TaskView
from domain.errors import TaskErrors
from domain.value_objects.task_id import TaskId


@dataclass(frozen=True)
class GetTaskQuery:
    task_id: UUID


class GetTaskQueryHandler:
    def __init__(self, reader: TaskReader) -> None:
        self._reader = reader

    async def execute(self, query: GetTaskQuery) -> Result[TaskView]:
        task = await self._reader.get(TaskId(query.task_id))
        if task is None:
            return Result[TaskView].fail(TaskErrors.not_found())
        return Result[TaskView].ok(TaskView.from_domain(task))
