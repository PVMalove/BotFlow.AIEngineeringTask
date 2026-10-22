"""Request DTOs: the HTTP shape of a call and its translation to a command or query."""

from uuid import UUID

from kernel_platform.security import Actor
from pydantic import BaseModel, Field

from application.commands.change_task_status import ChangeTaskStatusCommand
from application.queries.list_tasks import ListTasksQuery
from domain.tasks import TaskStatus

DEFAULT_LIMIT = 50
MAX_LIMIT = 100


class TaskListRequest(BaseModel):
    status: TaskStatus | None = None
    limit: int = Field(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    cursor: str | None = None

    def to_query(self) -> ListTasksQuery:
        return ListTasksQuery(status=self.status, limit=self.limit, cursor=self.cursor)


class ChangeStatusBody(BaseModel):
    status: TaskStatus
    # the tasks.version column is a 32-bit integer
    expected_version: int = Field(ge=1, le=2**31 - 1)

    def to_command(self, task_id: UUID, actor: Actor) -> ChangeTaskStatusCommand:
        return ChangeTaskStatusCommand(
            task_id=task_id,
            status=self.status,
            expected_version=self.expected_version,
            actor=actor,
        )
