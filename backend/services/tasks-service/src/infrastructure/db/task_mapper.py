"""Mapping of a stored task row to the Task aggregate."""

from domain.entities.task import Task
from domain.value_objects.task_id import TaskId
from domain.value_objects.task_source import TaskSource
from domain.value_objects.task_status import TaskStatus
from infrastructure.db.models import TaskModel


def to_task(row: TaskModel) -> Task:
    return Task.reconstitute(
        TaskId(row.id),
        text=row.text,
        status=TaskStatus(row.status),
        version=row.version,
        author_id=row.author_id,
        author_telegram_id=row.author_telegram_id,
        author_name=row.author_name,
        source_key=row.source_key,
        correlation_id=row.correlation_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        source_type=TaskSource(row.source_type) if row.source_type else None,
    )
