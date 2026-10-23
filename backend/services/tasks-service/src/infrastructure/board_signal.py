"""Board change signal through Redis Pub/Sub; the browser re-reads REST on it."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from kernel_domain.domain_event import DomainEvent
from redis.asyncio import Redis
from redis.asyncio.client import PubSub

from domain.events.task_domain_event import TaskCreated, TaskStatusChanged

CHANNEL = "botflow.board.changed"

logger = logging.getLogger(__name__)


class RedisBoardSignal:
    """Handler of `TaskCreated` and `TaskStatusChanged`, run after the commit."""

    def __init__(self, redis: Redis, timeout: float = 1.0) -> None:
        self._redis = redis
        self._timeout = timeout

    async def handle(self, event: DomainEvent) -> None:
        if not isinstance(event, TaskCreated | TaskStatusChanged):
            return
        # Pub/Sub never replays; a lost signal is covered by the panel's refresh.
        try:
            # Bounded: the change is already committed, so a hung Redis must not
            # keep the client waiting for its answer.
            async with asyncio.timeout(self._timeout):
                await self._redis.publish(
                    CHANNEL,
                    json.dumps(
                        {"type": "board.changed", "task_id": str(event.task_id.value)}
                    ),
                )
        except Exception:
            logger.warning("board.changed signal was not published", exc_info=False)


SUBSCRIBE_TIMEOUT = 2.0


class RedisBoardSubscription:
    def __init__(self, pubsub: PubSub) -> None:
        self._pubsub = pubsub

    async def wait(self, timeout: float) -> bool:
        message = await self._pubsub.get_message(
            ignore_subscribe_messages=True, timeout=timeout
        )
        return message is not None


class RedisBoardFeed:
    """Reads the board signal; each subscriber owns a Redis subscription.

    Pub/Sub keeps no history: what is published while nobody listens is gone.
    """

    def __init__(self, redis: Redis, subscribe_timeout: float = SUBSCRIBE_TIMEOUT):
        self._redis = redis
        self._subscribe_timeout = subscribe_timeout

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[RedisBoardSubscription]:
        pubsub = self._redis.pubsub()
        try:
            await pubsub.subscribe(CHANNEL)
            # Wait for Redis to confirm: signals published after this are not lost.
            await pubsub.get_message(
                ignore_subscribe_messages=False, timeout=self._subscribe_timeout
            )
            yield RedisBoardSubscription(pubsub)
        finally:
            await pubsub.aclose()  # type: ignore[no-untyped-call]
