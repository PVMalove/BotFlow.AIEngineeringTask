"""Identity persistence adapter, used within the caller's session."""

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infrastructure.db.models import (
    RateLimitModel,
)


class SqlRateLimiter:
    """Fixed-window counters in PostgreSQL; they survive restarts and rollbacks."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], clock: Callable[[], datetime]
    ) -> None:
        self._sessions = sessions
        self._clock = clock

    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        seconds = int(self._clock().timestamp())
        window = datetime.fromtimestamp(seconds - seconds % window_seconds, UTC)
        async with self._sessions() as session:
            count = await session.scalar(
                insert(RateLimitModel)
                .values(key=key, window_start=window, count=1)
                .on_conflict_do_update(
                    index_elements=[RateLimitModel.key, RateLimitModel.window_start],
                    set_={"count": RateLimitModel.count + 1},
                )
                .returning(RateLimitModel.count)
            )
            await session.execute(
                delete(RateLimitModel).where(
                    RateLimitModel.key == key, RateLimitModel.window_start < window
                )
            )
            await session.commit()
        return bool(count is not None and count <= limit)
