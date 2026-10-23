"""Board reads: keyset pagination over (created_at, id), newest first."""

from kernel_platform.pagination import Cursor
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.entities.task import Task
from domain.value_objects.task_id import TaskId
from domain.value_objects.task_status import TaskStatus
from infrastructure.db.models import TaskModel
from infrastructure.db.task_mapper import to_task


class SqlTaskReader:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_page(
        self, status: TaskStatus | None, after: Cursor | None, limit: int
    ) -> list[Task]:
        query = select(TaskModel).order_by(
            TaskModel.created_at.desc(), TaskModel.id.desc()
        )
        if status is not None:
            query = query.where(TaskModel.status == status.value)
        if after is not None:
            query = query.where(
                tuple_(TaskModel.created_at, TaskModel.id)
                < (after.created_at, after.id)
            )
        async with self._sessions() as session:
            rows = await session.scalars(query.limit(limit))
            return [to_task(row) for row in rows]

    async def get(self, task_id: TaskId) -> Task | None:
        async with self._sessions() as session:
            row = await session.get(TaskModel, task_id.value)
        return to_task(row) if row else None
