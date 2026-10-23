"""The Task aggregate in PostgreSQL."""

from collections.abc import Callable

from kernel_domain.entity import Entity
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from domain.entities.task import Task
from domain.value_objects.task_id import TaskId
from infrastructure.db.models import TaskModel
from infrastructure.db.task_reader import to_task


class SqlTaskRepository:
    def __init__(self, session: AsyncSession, track: Callable[[Entity[TaskId]], None]):
        self._session = session
        self._track = track

    async def get(self, task_id: TaskId) -> Task | None:
        row = await self._session.get(TaskModel, task_id.value)
        if row is None:
            return None
        task = to_task(row)
        self._track(task)
        return task

    async def add(self, task: Task) -> bool:
        # The unique source makes a second message for it a no-op; a task that was
        # not stored is not tracked, so it raises no event either.
        inserted = await self._session.execute(
            insert(TaskModel)
            .values(
                id=task.id.value,
                source_key=task.source_key,
                source_type=task.source_type.value if task.source_type else None,
                correlation_id=task.correlation_id,
                text=task.text,
                status=task.status.value,
                version=task.version,
                author_id=task.author_id,
                author_telegram_id=task.author_telegram_id,
                author_name=task.author_name,
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
            .on_conflict_do_nothing()
            .returning(TaskModel.id)
        )
        if inserted.scalar_one_or_none() is None:
            return False
        self._track(task)
        return True

    async def update_status(self, task: Task, *, loaded_version: int) -> bool:
        # The version comparison lives in the UPDATE's own WHERE, so a concurrent
        # writer can never slip between a check and the write.
        updated = await self._session.execute(
            update(TaskModel)
            .where(TaskModel.id == task.id.value, TaskModel.version == loaded_version)
            .values(
                status=task.status.value,
                version=task.version,
                updated_at=task.updated_at,
            )
            .returning(TaskModel.id)
        )
        return updated.scalar_one_or_none() is not None
