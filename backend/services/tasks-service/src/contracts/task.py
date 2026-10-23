"""Public representation of a task."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from domain.entities.task import Task


@dataclass(frozen=True)
class TaskView:
    id: UUID
    text: str
    status: str
    version: int
    author_id: UUID
    author_name: str
    created_at: datetime
    updated_at: datetime
    source_type: str | None = None

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
            source_type=task.source_type.value if task.source_type else None,
        )
