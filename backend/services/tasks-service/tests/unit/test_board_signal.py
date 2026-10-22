"""The Redis handler publishes the board signal and never lets a failure out."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

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


async def test_other_events_raise_no_signal() -> None:
    redis = AsyncMock()

    await RedisBoardSignal(redis).handle(
        TaskCreated(task_id=TaskId(uuid4()), correlation_id=uuid4(), source_key="1:2:3")
    )

    redis.publish.assert_not_awaited()


async def test_a_redis_failure_is_swallowed() -> None:
    redis = AsyncMock()
    redis.publish.side_effect = OSError("redis down")

    await RedisBoardSignal(redis).handle(changed(TaskId(uuid4())))  # must not raise


async def test_a_hung_redis_does_not_hold_the_caller() -> None:
    redis = AsyncMock()

    async def hang(*args: object) -> None:
        await asyncio.sleep(30)

    redis.publish.side_effect = hang

    await asyncio.wait_for(
        RedisBoardSignal(redis, timeout=0.05).handle(changed(TaskId(uuid4()))), 2
    )
