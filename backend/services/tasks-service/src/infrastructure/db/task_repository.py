"""The Task aggregate in PostgreSQL."""

from collections.abc import Callable
from datetime import datetime

from kernel_domain.entity import Entity
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from domain.entities.task import Task
from domain.value_objects.task_id import TaskId
from infrastructure.db.tables import inbox_messages, tasks
from infrastructure.db.task_reader import to_task


class SqlTaskRepository:
    def __init__(self, session: AsyncSession, track: Callable[[Entity[TaskId]], None]):
        self._session = session
        self._track = track

    async def get(self, task_id: TaskId) -> Task | None:
        row = (
            await self._session.execute(
                select(tasks).where(tasks.c.id == task_id.value)
            )
        ).one_or_none()
        if row is None:
            return None
        task = to_task(row)
        self._track(task)
        return task

    async def add(self, task: Task) -> bool:
        # The unique source makes a second message for it a no-op; a task that was
        # not stored is not tracked, so it raises no event either.
        inserted = await self._session.execute(
            insert(tasks)
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
            .returning(tasks.c.id)
        )
        if inserted.scalar_one_or_none() is None:
            return False
        self._track(task)
        return True

    async def update_status(self, task: Task, *, loaded_version: int) -> bool:
        # The version comparison lives in the UPDATE's own WHERE, so a concurrent
        # writer can never slip between a check and the write.
        updated = await self._session.execute(
            update(tasks)
            .where(tasks.c.id == task.id.value, tasks.c.version == loaded_version)
            .values(
                status=task.status.value,
                version=task.version,
                updated_at=task.updated_at,
            )
            .returning(tasks.c.id)
        )
        return updated.scalar_one_or_none() is not None


class SqlInboxRepository:
    def __init__(self, session: AsyncSession, now: Callable[[], datetime]) -> None:
        self._session = session
        self._now = now

    async def claim(self, message_id: str) -> bool:
        claimed = await self._session.execute(
            insert(inbox_messages)
            .values(message_id=message_id, received_at=self._now())
            .on_conflict_do_nothing(index_elements=[inbox_messages.c.message_id])
            .returning(inbox_messages.c.message_id)
        )
        return claimed.scalar_one_or_none() is not None
