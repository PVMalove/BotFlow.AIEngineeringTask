"""Board change signal through Redis Pub/Sub; the browser re-reads REST on it."""

import asyncio
import json
import logging

from kernel_domain.domain_event import DomainEvent
from redis.asyncio import Redis

from domain.events.task_domain_event import TaskStatusChanged

CHANNEL = "botflow.board.changed"

logger = logging.getLogger(__name__)


class RedisBoardSignal:
    """Handler of `TaskStatusChanged`, run after the change is committed."""

    def __init__(self, redis: Redis, timeout: float = 1.0) -> None:
        self._redis = redis
        self._timeout = timeout

    async def handle(self, event: DomainEvent) -> None:
        if not isinstance(event, TaskStatusChanged):
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
