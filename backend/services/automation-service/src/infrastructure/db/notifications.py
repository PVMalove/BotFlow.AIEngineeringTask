"""Consumer inbox and notification intents; both are written in one transaction."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    Table,
    Text,
    Uuid,
    case,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.notifications import IntentStatus, NotificationIntent
from infrastructure.db.inbox import metadata

inbox_messages = Table(
    "inbox_messages",
    metadata,
    Column("message_id", Text, primary_key=True),
    Column("received_at", DateTime(timezone=True), nullable=False),
)

notification_intents = Table(
    "notification_intents",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("chat_id", BigInteger, nullable=False),
    Column("member_id", Uuid, nullable=False),
    Column("kind", Text, nullable=False),
    Column("access_version", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "status IN ('pending', 'enqueued', 'sent', 'suppressed_stale', "
        "'suppressed_blocked', 'failed')",
        name="notification_intents_status_check",
    ),
    Index(
        "notification_intents_pending",
        "created_at",
        postgresql_where=text("status = 'pending'"),
    ),
)

_OPEN = (IntentStatus.PENDING.value, IntentStatus.ENQUEUED.value)


class SqlIntentStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def store_event(self, message_id: str, intent: NotificationIntent) -> bool:
        now = datetime.now(UTC)
        async with self._sessions() as session:
            claimed = await session.execute(
                insert(inbox_messages)
                .values(message_id=message_id, received_at=now)
                .on_conflict_do_nothing(index_elements=[inbox_messages.c.message_id])
                .returning(inbox_messages.c.message_id)
            )
            if claimed.scalar_one_or_none() is None:
                return False  # redelivery: the first copy already made its intent
            await session.execute(
                notification_intents.insert().values(
                    id=intent.id,
                    chat_id=intent.chat_id,
                    member_id=intent.member_id,
                    kind=intent.kind,
                    access_version=intent.access_version,
                    status=intent.status.value,
                    attempts=intent.attempts,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()  # the broker message is acknowledged only after this
        return True

    async def pending(self, limit: int) -> list[UUID]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(notification_intents.c.id)
                .where(notification_intents.c.status == IntentStatus.PENDING.value)
                .order_by(notification_intents.c.created_at, notification_intents.c.id)
                .limit(limit)
            )
            return list(rows)

    async def mark_enqueued(self, intent_id: UUID) -> None:
        await self._update(
            intent_id, (IntentStatus.PENDING.value,), IntentStatus.ENQUEUED
        )

    async def get(self, intent_id: UUID) -> NotificationIntent | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(notification_intents).where(
                        notification_intents.c.id == intent_id
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        return NotificationIntent(
            id=row.id,
            chat_id=row.chat_id,
            member_id=row.member_id,
            kind=row.kind,
            access_version=row.access_version,
            status=IntentStatus(row.status),
            attempts=row.attempts,
        )

    async def settle(self, intent_id: UUID, status: IntentStatus) -> None:
        await self._update(intent_id, _OPEN, status)

    async def record_failure(self, intent_id: UUID, max_attempts: int) -> IntentStatus:
        table = notification_intents
        async with self._sessions() as session:
            status = await session.scalar(
                update(table)
                .where(table.c.id == intent_id, table.c.status.in_(_OPEN))
                .values(
                    attempts=table.c.attempts + 1,
                    status=case(
                        (
                            table.c.attempts + 1 >= max_attempts,
                            IntentStatus.FAILED.value,
                        ),
                        else_=table.c.status,
                    ),
                    updated_at=datetime.now(UTC),
                )
                .returning(table.c.status)
            )
            await session.commit()
        return IntentStatus(status) if status else IntentStatus.FAILED

    async def _update(
        self, intent_id: UUID, from_statuses: tuple[str, ...], to: IntentStatus
    ) -> None:
        table = notification_intents
        async with self._sessions() as session:
            await session.execute(
                update(table)
                .where(table.c.id == intent_id, table.c.status.in_(from_statuses))
                .values(status=to.value, updated_at=datetime.now(UTC))
            )
            await session.commit()
