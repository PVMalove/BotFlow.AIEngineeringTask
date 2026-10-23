"""The Redis handler publishes the board signal and never lets a failure out."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from kernel_domain.domain_event import DomainEvent
from kernel_platform.resources import Resources

from api.containers import tasks_unit_of_work
from domain.entities.task import Task
from domain.events.task_domain_event import TaskCreated, TaskStatusChanged
from domain.value_objects.task_id import TaskId
from domain.value_objects.task_status import TaskStatus
from infrastructure.board_signal import CHANNEL, RedisBoardSignal


def changed(task_id: TaskId) -> TaskStatusChanged:
    return TaskStatusChanged(
        task_id=task_id,
        previous_status=TaskStatus.PENDING,
        status=TaskStatus.COMPLETED,
        version=2,
    )


async def test_the_task_id_is_published_on_the_board_channel() -> None:
    redis = AsyncMock()
    task_id = TaskId(uuid4())

    await RedisBoardSignal(redis).handle(changed(task_id))

    channel, message = redis.publish.await_args.args
    assert channel == CHANNEL == "botflow.board.changed"
    assert str(task_id.value) in message


async def test_a_created_task_is_published_in_the_same_format() -> None:
    redis = AsyncMock()
    task_id = TaskId(uuid4())

    await RedisBoardSignal(redis).handle(
        TaskCreated(task_id=task_id, correlation_id=uuid4(), source_key="1:2:3")
    )

    channel, message = redis.publish.await_args.args
    assert channel == CHANNEL
    assert json.loads(message) == {
        "type": "board.changed",
        "task_id": str(task_id.value),
    }


async def test_an_event_that_is_not_a_task_change_raises_no_signal() -> None:
    redis = AsyncMock()

    await RedisBoardSignal(redis).handle(DomainEvent())

    redis.publish.assert_not_awaited()


async def test_a_redis_failure_is_swallowed() -> None:
    redis = AsyncMock()
    redis.publish.side_effect = OSError("redis down")

    await RedisBoardSignal(redis).handle(changed(TaskId(uuid4())))  # must not raise


async def test_a_redis_failure_on_creation_is_swallowed() -> None:
    redis = AsyncMock()
    redis.publish.side_effect = OSError("redis down")

    await RedisBoardSignal(redis).handle(
        TaskCreated(task_id=TaskId(uuid4()), correlation_id=uuid4(), source_key="1:2:3")
    )  # must not raise


async def test_a_hung_redis_does_not_hold_the_caller() -> None:
    redis = AsyncMock()

    async def hang(*args: object) -> None:
        await asyncio.sleep(30)

    redis.publish.side_effect = hang

    await asyncio.wait_for(
        RedisBoardSignal(redis, timeout=0.05).handle(changed(TaskId(uuid4()))), 2
    )


class FakeResult:
    def scalar_one_or_none(self) -> bool:
        return True  # the outbox row was staged


class FakeSession:
    """A session that accepts the outbox insert and the commit, nothing else."""

    def __init__(self) -> None:
        self.committed = False

    async def execute(self, statement: Any) -> FakeResult:
        return FakeResult()

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass

    async def close(self) -> None:
        pass


async def test_the_worker_and_the_api_share_a_factory_that_signals_after_commit() -> (
    None
):
    redis = AsyncMock()
    session = FakeSession()
    resources = Resources(
        engine=MagicMock(),
        sessions=MagicMock(return_value=session),
        rabbitmq=MagicMock(),
        redis=redis,
    )
    task = Task.create(
        TaskId(uuid4()),
        text="Купить молоко",
        author_id=uuid4(),
        author_telegram_id=1,
        author_name="Ann (@ann)",
        source_key="1:2:3",
        correlation_id=uuid4(),
        now=datetime(2026, 1, 1, tzinfo=UTC),
    ).value

    async with tasks_unit_of_work(resources)() as uow:
        uow.track(task)
        await uow.commit()

    assert session.committed
    redis.publish.assert_awaited_once()
    assert redis.publish.await_args.args[0] == CHANNEL
