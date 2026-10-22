"""Board reads: keyset pagination over (created_at, id), newest first."""

from uuid import UUID

from kernel_platform.pagination import Cursor
from sqlalchemy import select, tuple_
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.tasks import Task, TaskStatus
from infrastructure.db.tables import tasks


def to_task(row: Row[tuple[object, ...]]) -> Task:
    return Task(
        id=row.id,
        text=row.text,
        status=TaskStatus(row.status),
        version=row.version,
        author_id=row.author_id,
        author_name=row.author_name,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlTaskReader:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_page(
        self, status: TaskStatus | None, after: Cursor | None, limit: int
    ) -> list[Task]:
        query = select(tasks).order_by(tasks.c.created_at.desc(), tasks.c.id.desc())
        if status is not None:
            query = query.where(tasks.c.status == status.value)
        if after is not None:
            query = query.where(
                tuple_(tasks.c.created_at, tasks.c.id) < (after.created_at, after.id)
            )
        async with self._sessions() as session:
            rows = await session.execute(query.limit(limit))
            return [to_task(row) for row in rows]

    async def get(self, task_id: UUID) -> Task | None:
        async with self._sessions() as session:
            row = (
                await session.execute(select(tasks).where(tasks.c.id == task_id))
            ).one_or_none()
        return to_task(row) if row else None
