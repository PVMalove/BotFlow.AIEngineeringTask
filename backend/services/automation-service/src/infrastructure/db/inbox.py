"""Telegram update inbox: an update is processed at most once per `update_id`."""

from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infrastructure.db.models import Base

metadata = Base.metadata
telegram_updates = metadata.tables["telegram_updates"]


class SqlUpdateInbox:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def claim(self, update_id: int) -> bool:
        """Record the update; False when it was already claimed."""
        async with self._sessions() as session:
            result = await session.execute(
                insert(telegram_updates)
                .values(update_id=update_id, received_at=datetime.now(UTC))
                .on_conflict_do_nothing(index_elements=[telegram_updates.c.update_id])
                .returning(telegram_updates.c.update_id)
            )
            claimed = result.scalar_one_or_none() is not None
            await session.commit()
        return claimed
