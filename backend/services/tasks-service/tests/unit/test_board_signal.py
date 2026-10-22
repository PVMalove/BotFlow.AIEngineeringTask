"""The Redis adapter publishes the board signal and never lets a failure out."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

from infrastructure.board_signal import CHANNEL, RedisBoardSignal


async def test_the_task_id_is_published_on_the_board_channel() -> None:
    redis = AsyncMock()
    task_id = uuid4()

    await RedisBoardSignal(redis).changed(task_id)

    channel, message = redis.publish.await_args.args
    assert channel == CHANNEL == "botflow.board.changed"
    assert str(task_id) in message


async def test_a_redis_failure_is_swallowed() -> None:
    redis = AsyncMock()
    redis.publish.side_effect = OSError("redis down")

    await RedisBoardSignal(redis).changed(uuid4())  # must not raise


async def test_a_hung_redis_does_not_hold_the_caller() -> None:
    redis = AsyncMock()

    async def hang(*args: object) -> None:
        await asyncio.sleep(30)

    redis.publish.side_effect = hang

    await asyncio.wait_for(RedisBoardSignal(redis, timeout=0.05).changed(uuid4()), 2)
