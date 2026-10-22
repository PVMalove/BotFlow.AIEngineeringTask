"""Telegram update inbox: an update is processed at most once per `update_id`."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Column, DateTime, MetaData, Table
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

metadata = MetaData()

telegram_updates = Table(
    "telegram_updates",
    metadata,
    Column("update_id", BigInteger, primary_key=True, autoincrement=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
)


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
