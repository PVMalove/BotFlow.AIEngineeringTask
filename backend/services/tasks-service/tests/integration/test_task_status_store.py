"""SqlTaskStatusStore on real PostgreSQL: atomic compare-and-update, two sessions.

Runs only when BOTFLOW_TEST_DATABASE_URL names a disposable database, e.g.
postgresql+asyncpg://user:password@localhost:5432/test.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from application.ports import TaskNotFound, VersionConflict
from domain.tasks import TaskStatus
from infrastructure.db.tables import metadata, tasks
from infrastructure.db.task_store import SqlTaskStatusStore

DATABASE_URL = os.environ.get("BOTFLOW_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="BOTFLOW_TEST_DATABASE_URL is not set"
)

CREATED = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class Signal:
    def __init__(self) -> None:
        self.sent: list[UUID] = []

    async def changed(self, task_id: UUID) -> None:
        self.sent.append(task_id)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    assert DATABASE_URL
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def task_id(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[UUID]:
    new_id = uuid4()
    async with sessions() as session:
        await session.execute(
            insert(tasks).values(
                id=new_id,
                source_key=f"it:{new_id}",
                correlation_id=uuid4(),
                text="text",
                status="pending",
                version=1,
                author_id=uuid4(),
                author_telegram_id=1,
                author_name="Ann",
                created_at=CREATED,
                updated_at=CREATED,
            )
        )
        await session.commit()
    yield new_id
    async with sessions() as session:
        await session.execute(delete(tasks).where(tasks.c.id == new_id))
        await session.commit()


async def stored(sessions: async_sessionmaker[AsyncSession], task_id: UUID) -> tuple:  # type: ignore[type-arg]
    async with sessions() as session:
        row = (await session.execute(select(tasks).where(tasks.c.id == task_id))).one()
    return row.status, row.version, row.updated_at


async def test_a_real_change_bumps_the_version_once_and_signals_after_commit(
    sessions: async_sessionmaker[AsyncSession], task_id: UUID
) -> None:
    signal = Signal()

    task = await SqlTaskStatusStore(sessions, signal).change(
        task_id, TaskStatus.IN_PROGRESS, 1
    )

    assert (task.status, task.version) == (TaskStatus.IN_PROGRESS, 2)
    assert task.updated_at > CREATED
    assert await stored(sessions, task_id) == ("in_progress", 2, task.updated_at)
    assert signal.sent == [task_id]


async def test_a_no_op_with_the_current_version_changes_nothing_and_stays_silent(
    sessions: async_sessionmaker[AsyncSession], task_id: UUID
) -> None:
    signal = Signal()

    task = await SqlTaskStatusStore(sessions, signal).change(
        task_id, TaskStatus.PENDING, 1
    )

    assert (task.status, task.version, task.updated_at) == (
        TaskStatus.PENDING,
        1,
        CREATED,
    )
    assert await stored(sessions, task_id) == ("pending", 1, CREATED)
    assert signal.sent == []


async def test_a_stale_version_conflicts_even_when_the_target_equals_the_status(
    sessions: async_sessionmaker[AsyncSession], task_id: UUID
) -> None:
    store = SqlTaskStatusStore(sessions, Signal())
    await store.change(task_id, TaskStatus.COMPLETED, 1)

    for target in (TaskStatus.COMPLETED, TaskStatus.PENDING):
        with pytest.raises(VersionConflict):
            await store.change(task_id, target, 1)

    assert (await stored(sessions, task_id))[:2] == ("completed", 2)


async def test_a_missing_task_is_not_found(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(TaskNotFound):
        await SqlTaskStatusStore(sessions, Signal()).change(
            uuid4(), TaskStatus.COMPLETED, 1
        )


async def test_two_concurrent_sessions_give_exactly_one_success(
    sessions: async_sessionmaker[AsyncSession], task_id: UUID
) -> None:
    signal = Signal()
    store = SqlTaskStatusStore(sessions, signal)

    results = await asyncio.gather(
        *(
            store.change(task_id, target, 1)
            for target in (TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED)
        ),
        return_exceptions=True,
    )

    winners = [r for r in results if not isinstance(r, BaseException)]
    losers = [r for r in results if isinstance(r, VersionConflict)]
    assert len(winners) == 1 and len(losers) == 1
    assert (await stored(sessions, task_id))[:2] == (winners[0].status.value, 2)
    assert signal.sent == [task_id]


async def test_many_concurrent_writers_with_one_version_leave_one_change(
    sessions: async_sessionmaker[AsyncSession], task_id: UUID
) -> None:
    store = SqlTaskStatusStore(sessions, Signal())

    results = await asyncio.gather(
        *(store.change(task_id, TaskStatus.COMPLETED, 1) for _ in range(8)),
        return_exceptions=True,
    )

    # Same target: one real change, the rest either lose the race (409) or are the
    # no-op of an already-moved task; the version is bumped exactly once.
    assert not [
        r
        for r in results
        if isinstance(r, Exception) and not isinstance(r, VersionConflict)
    ]
    assert (await stored(sessions, task_id))[:2] == ("completed", 2)


async def test_a_failed_commit_leaves_no_partial_change_and_no_signal(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal = Signal()

    async def broken_commit(self: AsyncSession) -> None:
        raise OSError("connection lost")

    monkeypatch.setattr(AsyncSession, "commit", broken_commit)
    with pytest.raises(OSError):
        await SqlTaskStatusStore(sessions, signal).change(
            task_id, TaskStatus.COMPLETED, 1
        )
    monkeypatch.undo()

    assert await stored(sessions, task_id) == ("pending", 1, CREATED)
    assert signal.sent == []
