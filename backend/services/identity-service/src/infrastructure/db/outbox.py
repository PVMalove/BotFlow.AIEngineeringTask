"""The identity outbox table as the relay's repository."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from domain.outbox import OutboxEntry
from infrastructure.db.tables import outbox


class SqlOutbox:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entry: OutboxEntry) -> None:
        await self._session.execute(
            outbox.insert().values(
                id=entry.id,
                type=entry.type,
                payload=entry.payload,
                created_at=entry.created_at,
                attempts=entry.attempts,
                next_attempt_at=entry.next_attempt_at,
            )
        )

    async def claim_due(self, now: datetime, limit: int) -> list[OutboxEntry]:
        # SKIP LOCKED lets several relays run without publishing a row twice.
        rows = await self._session.execute(
            select(outbox)
            .where(outbox.c.published_at.is_(None), outbox.c.next_attempt_at <= now)
            .order_by(outbox.c.created_at, outbox.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [
            OutboxEntry(
                id=row.id,
                type=row.type,
                payload=row.payload,
                created_at=row.created_at,
                attempts=row.attempts,
                next_attempt_at=row.next_attempt_at,
                published_at=row.published_at,
            )
            for row in rows
        ]

    async def mark_published(self, entry_id: UUID, now: datetime) -> None:
        await self._session.execute(
            update(outbox).where(outbox.c.id == entry_id).values(published_at=now)
        )

    async def reschedule(
        self, entry_id: UUID, attempts: int, next_attempt_at: datetime | None
    ) -> None:
        await self._session.execute(
            update(outbox)
            .where(outbox.c.id == entry_id)
            .values(attempts=attempts, next_attempt_at=next_attempt_at)
        )
