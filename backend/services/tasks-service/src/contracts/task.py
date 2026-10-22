"""Public representation of a task."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from domain.entities.task import Task


class TaskView(BaseModel):
    id: UUID
    text: str
    status: str
    version: int
    author_id: UUID
    author_name: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, task: Task) -> "TaskView":
        return cls(
            id=task.id.value,
            text=task.text,
            status=task.status.value,
            version=task.version,
            author_id=task.author_id,
            author_name=task.author_name,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )
