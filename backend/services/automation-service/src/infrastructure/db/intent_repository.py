"""NotificationIntent aggregates and the consumer inbox in PostgreSQL."""

from collections.abc import Callable
from datetime import datetime

from kernel_domain.entity import Entity
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from domain.entities.notification_intent import NotificationIntent
from domain.value_objects.intent_id import IntentId
from domain.value_objects.intent_status import IntentStatus
from infrastructure.db.models import NotificationIntentModel


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
            await self._session.scalars(
                select(NotificationIntentModel).where(
                    NotificationIntentModel.id == intent_id.value
                )
            )
        ).one_or_none()
        return _intent(row) if row else None

    async def add(self, intent: NotificationIntent) -> bool:
        now = self._clock()
        added = await self._session.execute(
            insert(NotificationIntentModel)
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
            .on_conflict_do_nothing(index_elements=[NotificationIntentModel.id])
            .returning(NotificationIntentModel.id)
        )
        if added.scalar_one_or_none() is None:
            return False
        self._track(intent)
        return True

    async def pending(self, limit: int) -> list[IntentId]:
        rows = await self._session.scalars(
            select(NotificationIntentModel.id)
            .where(NotificationIntentModel.status == IntentStatus.PENDING.value)
            .order_by(NotificationIntentModel.created_at, NotificationIntentModel.id)
            .limit(limit)
        )
        return [IntentId(row) for row in rows]

    async def update(self, intent: NotificationIntent) -> None:
        await self._session.execute(
            update(NotificationIntentModel)
            .where(NotificationIntentModel.id == intent.id.value)
            .values(
                status=intent.status.value,
                attempts=intent.attempts,
                updated_at=self._clock(),
            )
        )
        self._track(intent)


def _intent(row: NotificationIntentModel) -> NotificationIntent:
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
