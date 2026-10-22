"""The task commands on real PostgreSQL: atomic compare-and-update, two sessions.

Runs only when BOTFLOW_TEST_DATABASE_URL names a disposable database, e.g.
postgresql+asyncpg://user:password@localhost:5432/test.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from kernel_domain.domain_event import DomainEvent
from kernel_platform.security import Actor, ActorRole
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from application.commands.change_task_status import (
    ChangeTaskStatusCommand,
    ChangeTaskStatusCommandHandler,
)
from application.commands.create_task import (
    CreateTaskCommand,
    CreateTaskCommandHandler,
)
from domain.events.task_domain_event import TaskStatusChanged
from domain.value_objects.task_status import TaskStatus
from infrastructure.db.tables import inbox_messages, metadata, outbox, tasks
from infrastructure.db.unit_of_work import SqlTasksUnitOfWork

DATABASE_URL = os.environ.get("BOTFLOW_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="BOTFLOW_TEST_DATABASE_URL is not set"
)

CREATED = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
AUTHOR = uuid4()
ADMIN = Actor(uuid4(), ActorRole.ADMIN)


class Signals:
    """The in-process handler that runs after a commit."""

    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def __call__(self, event: DomainEvent) -> None:
        self.events.append(event)

    @property
    def changed(self) -> list[UUID]:
        return [
            e.task_id.value for e in self.events if isinstance(e, TaskStatusChanged)
        ]


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
def signals() -> Signals:
    return Signals()


@pytest.fixture
def change(
    sessions: async_sessionmaker[AsyncSession], signals: Signals
) -> ChangeTaskStatusCommandHandler:
    return ChangeTaskStatusCommandHandler(
        lambda: SqlTasksUnitOfWork(sessions, event_handlers=[signals])
    )


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
                author_id=AUTHOR,
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


def command(task_id: UUID, target: TaskStatus, version: int) -> ChangeTaskStatusCommand:
    return ChangeTaskStatusCommand(task_id, target, version, ADMIN)


async def stored(
    sessions: async_sessionmaker[AsyncSession], task_id: UUID
) -> tuple[str, int, datetime]:
    async with sessions() as session:
        row = (await session.execute(select(tasks).where(tasks.c.id == task_id))).one()
    return row.status, row.version, row.updated_at


async def test_a_real_change_bumps_the_version_once_and_signals_after_commit(
    sessions: async_sessionmaker[AsyncSession],
    change: ChangeTaskStatusCommandHandler,
    signals: Signals,
    task_id: UUID,
) -> None:
    result = await change.execute(command(task_id, TaskStatus.IN_PROGRESS, 1))

    task = result.value
    assert (task.status, task.version) == ("in_progress", 2)
    assert task.updated_at > CREATED
    assert await stored(sessions, task_id) == ("in_progress", 2, task.updated_at)
    assert signals.changed == [task_id]


async def test_a_no_op_with_the_current_version_changes_nothing_and_stays_silent(
    sessions: async_sessionmaker[AsyncSession],
    change: ChangeTaskStatusCommandHandler,
    signals: Signals,
    task_id: UUID,
) -> None:
    result = await change.execute(command(task_id, TaskStatus.PENDING, 1))

    task = result.value
    assert (task.status, task.version, task.updated_at) == ("pending", 1, CREATED)
    assert await stored(sessions, task_id) == ("pending", 1, CREATED)
    assert signals.events == []


async def test_a_stale_version_conflicts_even_when_the_target_equals_the_status(
    sessions: async_sessionmaker[AsyncSession],
    change: ChangeTaskStatusCommandHandler,
    task_id: UUID,
) -> None:
    await change.execute(command(task_id, TaskStatus.COMPLETED, 1))

    for target in (TaskStatus.COMPLETED, TaskStatus.PENDING):
        result = await change.execute(command(task_id, target, 1))
        assert result.is_err and result.error.code == "version_conflict"

    assert (await stored(sessions, task_id))[:2] == ("completed", 2)


async def test_a_missing_task_is_not_found(
    change: ChangeTaskStatusCommandHandler,
) -> None:
    result = await change.execute(command(uuid4(), TaskStatus.COMPLETED, 1))

    assert result.is_err and result.error.code == "task_not_found"


async def test_two_concurrent_sessions_give_exactly_one_success(
    sessions: async_sessionmaker[AsyncSession],
    change: ChangeTaskStatusCommandHandler,
    signals: Signals,
    task_id: UUID,
) -> None:
    results = await asyncio.gather(
        *(
            change.execute(command(task_id, target, 1))
            for target in (TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED)
        ),
    )

    winners = [r for r in results if r.is_ok]
    losers = [r for r in results if r.is_err and r.error.code == "version_conflict"]
    assert len(winners) == 1 and len(losers) == 1
    assert (await stored(sessions, task_id))[:2] == (winners[0].value.status, 2)
    assert signals.changed == [task_id]


async def test_many_concurrent_writers_with_one_version_leave_one_change(
    sessions: async_sessionmaker[AsyncSession],
    change: ChangeTaskStatusCommandHandler,
    task_id: UUID,
) -> None:
    results = await asyncio.gather(
        *(change.execute(command(task_id, TaskStatus.COMPLETED, 1)) for _ in range(8)),
    )

    # Same target: one real change, the rest lose the race (409); the version is
    # bumped exactly once.
    assert not [r for r in results if r.is_err and r.error.code != "version_conflict"]
    assert (await stored(sessions, task_id))[:2] == ("completed", 2)


async def test_a_failed_commit_leaves_no_partial_change_and_no_signal(
    sessions: async_sessionmaker[AsyncSession],
    change: ChangeTaskStatusCommandHandler,
    signals: Signals,
    task_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken_commit(self: AsyncSession) -> None:
        raise OSError("connection lost")

    monkeypatch.setattr(AsyncSession, "commit", broken_commit)
    with pytest.raises(OSError):
        await change.execute(command(task_id, TaskStatus.COMPLETED, 1))
    monkeypatch.undo()

    assert await stored(sessions, task_id) == ("pending", 1, CREATED)
    assert signals.events == []


async def test_creating_a_task_stores_it_once_with_one_result_message(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    job = uuid4()
    create = CreateTaskCommandHandler(lambda: SqlTasksUnitOfWork(sessions))
    new_command = CreateTaskCommand(
        message_id=f"it-{job}",
        correlation_id=job,
        bot_id=1,
        chat_id=job.int % 10**9,
        source_message_id=7,
        text="text",
        author_id=AUTHOR,
        author_telegram_id=1,
        author_name="Ann",
    )

    first = await create.execute(new_command)
    again = await create.execute(new_command)  # a redelivery

    assert first.value is True and again.value is False
    async with sessions() as session:
        stored_tasks = (
            await session.execute(select(tasks).where(tasks.c.correlation_id == job))
        ).all()
        queued = (
            await session.execute(select(outbox).where(outbox.c.correlation_id == job))
        ).all()
        await session.execute(delete(outbox).where(outbox.c.correlation_id == job))
        await session.execute(delete(tasks).where(tasks.c.correlation_id == job))
        await session.execute(
            delete(inbox_messages).where(inbox_messages.c.message_id == f"it-{job}")
        )
        await session.commit()
    assert len(stored_tasks) == 1 and len(queued) == 1
    assert queued[0].type == "TaskCreated" and queued[0].routing_key == "task.created"
