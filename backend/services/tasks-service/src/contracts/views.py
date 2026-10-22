"""Public representation of a task."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from domain.tasks import Task, TaskStatus


class TaskView(BaseModel):
    id: UUID
    text: str
    status: str
    version: int
    author_id: UUID
    author_name: str
    created_at: datetime
    updated_at: datetime


class ChangeStatusRequest(BaseModel):
    status: TaskStatus
    expected_version: int = Field(
        ge=1, le=2**31 - 1
    )  # the tasks.version column is a 32-bit integer


def task_view(task: Task) -> TaskView:
    return TaskView(
        id=task.id,
        text=task.text,
        status=task.status.value,
        version=task.version,
        author_id=task.author_id,
        author_name=task.author_name,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )
