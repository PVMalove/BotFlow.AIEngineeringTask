"""Board change signal through Redis Pub/Sub; the browser re-reads REST on it."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from kernel_domain.domain_event import DomainEvent
from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from redis.exceptions import RedisError

from application.ports import BoardFeedUnavailable
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
_REDIS_FAILURES = (RedisError, OSError, TimeoutError)


class RedisBoardSubscription:
    def __init__(self, pubsub: PubSub) -> None:
        self._pubsub = pubsub

    async def wait(self, timeout: float) -> bool:
        try:
            if await self._receive(timeout) is None:
                return False
            while await self._receive(0) is not None:
                pass  # the board is re-read once for a burst of changes
            return True
        except _REDIS_FAILURES as exc:
            raise BoardFeedUnavailable from exc

    async def _receive(self, timeout: float) -> object:
        return await self._pubsub.get_message(
            ignore_subscribe_messages=True, timeout=timeout
        )


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
            try:
                await pubsub.subscribe(CHANNEL)
                # Wait for Redis to confirm: signals published after this are not lost.
                confirmation = await pubsub.get_message(
                    ignore_subscribe_messages=False, timeout=self._subscribe_timeout
                )
            except _REDIS_FAILURES as exc:
                raise BoardFeedUnavailable from exc
            if confirmation is None:
                raise BoardFeedUnavailable
            yield RedisBoardSubscription(pubsub)
        finally:
            with suppress(*_REDIS_FAILURES):
                await pubsub.aclose()  # type: ignore[no-untyped-call]
