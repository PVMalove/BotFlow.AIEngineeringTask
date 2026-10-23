"""Telegram update inbox: an update is processed at most once per `update_id`."""

from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infrastructure.db.models import TelegramUpdateModel


class SqlUpdateInbox:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def claim(self, update_id: int) -> bool:
        """Record the update; False when it was already claimed."""
        async with self._sessions() as session:
            claimed = await session.scalar(
                insert(TelegramUpdateModel)
                .values(update_id=update_id, received_at=datetime.now(UTC))
                .on_conflict_do_nothing(index_elements=[TelegramUpdateModel.update_id])
                .returning(TelegramUpdateModel.update_id)
            )
            await session.commit()
        return claimed is not None
