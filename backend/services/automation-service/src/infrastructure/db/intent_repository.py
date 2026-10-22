"""NotificationIntent aggregates and the consumer inbox in PostgreSQL."""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from kernel_domain.entity import Entity
from sqlalchemy import Row, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from domain.entities.notification_intent import NotificationIntent
from domain.value_objects.intent_id import IntentId
from domain.value_objects.intent_status import IntentStatus
from infrastructure.db.tables import inbox_messages, notification_intents


class SqlIntentRepository:
    def __init__(
        self,
        session: AsyncSession,
        track: Callable[[Entity[IntentId]], None],
        clock: Callable[[], datetime],
    ) -> None:
        self._session = session
        self._track = track
        self._clock = clock

    async def get(self, intent_id: IntentId) -> NotificationIntent | None:
        row = (
            await self._session.execute(
                select(notification_intents).where(
                    notification_intents.c.id == intent_id.value
                )
            )
        ).one_or_none()
        return _intent(row) if row else None

    async def add(self, intent: NotificationIntent) -> bool:
        now = self._clock()
        added = await self._session.execute(
            insert(notification_intents)
            .values(
                id=intent.id.value,
                chat_id=intent.chat_id,
                member_id=intent.member_id,
                kind=intent.kind,
                access_version=intent.access_version,
                status=intent.status.value,
                attempts=intent.attempts,
                body=intent.body,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[notification_intents.c.id])
            .returning(notification_intents.c.id)
        )
        if added.scalar_one_or_none() is None:
            return False
        self._track(intent)
        return True

    async def pending(self, limit: int) -> list[IntentId]:
        rows = await self._session.scalars(
            select(notification_intents.c.id)
            .where(notification_intents.c.status == IntentStatus.PENDING.value)
            .order_by(notification_intents.c.created_at, notification_intents.c.id)
            .limit(limit)
        )
        return [IntentId(row) for row in rows]

    async def update(self, intent: NotificationIntent) -> None:
        await self._session.execute(
            update(notification_intents)
            .where(notification_intents.c.id == intent.id.value)
            .values(
                status=intent.status.value,
                attempts=intent.attempts,
                updated_at=self._clock(),
            )
        )
        self._track(intent)


class SqlInbox:
    def __init__(self, session: AsyncSession, clock: Callable[[], datetime]) -> None:
        self._session = session
        self._clock = clock

    async def claim(self, message_id: str) -> bool:
        claimed = await self._session.execute(
            insert(inbox_messages)
            .values(message_id=message_id, received_at=self._clock())
            .on_conflict_do_nothing(index_elements=[inbox_messages.c.message_id])
            .returning(inbox_messages.c.message_id)
        )
        return claimed.scalar_one_or_none() is not None


def _intent(row: Row[Any]) -> NotificationIntent:
    return NotificationIntent.reconstitute(
        IntentId(row.id),
        chat_id=row.chat_id,
        member_id=row.member_id,
        kind=row.kind,
        access_version=row.access_version,
        status=IntentStatus(row.status),
        attempts=row.attempts,
        body=row.body,
    )
