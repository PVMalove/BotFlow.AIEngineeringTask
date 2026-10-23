"""Mapped consumer inbox and atomic message claims in the caller's transaction."""

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import DateTime, Text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column


class InboxColumns:
    message_id: Mapped[str] = mapped_column(Text, primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SqlInboxRepository:
    def __init__(
        self,
        session: AsyncSession,
        clock: Callable[[], datetime],
        model: type[InboxColumns],
    ) -> None:
        self._session = session
        self._clock = clock
        self._model = model

    async def claim(self, message_id: str) -> bool:
        model = self._model
        claimed = await self._session.execute(
            insert(model)
            .values(message_id=message_id, received_at=self._clock())
            .on_conflict_do_nothing(index_elements=[model.message_id])
            .returning(model.message_id)
        )
        return claimed.scalar_one_or_none() is not None
