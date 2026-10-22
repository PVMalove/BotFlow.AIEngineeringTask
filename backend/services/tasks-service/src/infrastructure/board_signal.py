"""Board change signal through Redis Pub/Sub; the browser re-reads REST on it."""

import asyncio
import json
import logging
from uuid import UUID

from redis.asyncio import Redis

CHANNEL = "botflow.board.changed"

logger = logging.getLogger(__name__)


class RedisBoardSignal:
    def __init__(self, redis: Redis, timeout: float = 1.0) -> None:
        self._redis = redis
        self._timeout = timeout

    async def changed(self, task_id: UUID) -> None:
        # Pub/Sub never replays; a lost signal is covered by the panel's refresh.
        try:
            # Bounded: the change is already committed, so a hung Redis must not
            # keep the client waiting for its answer.
            async with asyncio.timeout(self._timeout):
                await self._redis.publish(
                    CHANNEL,
                    json.dumps({"type": "board.changed", "task_id": str(task_id)}),
                )
        except Exception:
            logger.warning("board.changed signal was not published", exc_info=False)
