"""Transactional outbox shared by Tasks and Automation, and its broker relay.

A producer inserts a row in its business transaction (`enqueue`). The relay claims
due rows with `FOR UPDATE SKIP LOCKED`, publishes each with a broker confirm and
stores the outcome in the same transaction: attempts, next attempt and the last
error stay in PostgreSQL. A row whose attempts are exhausted has no next attempt;
`on_exhausted` runs in that transaction, and `replay` makes the row due again.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from aiormq.exceptions import ChannelInvalidStateError
from sqlalchemy import (
    ColumnElement,
    DateTime,
    Index,
    Integer,
    Table,
    Text,
    Uuid,
    literal_column,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from kernel_platform.clock import Clock, utcnow
from kernel_platform.retry import RetryPolicy


@dataclass(frozen=True, slots=True)
class OutboxRow:
    id: UUID
    type: str
    routing_key: str
    payload: dict[str, Any]  # the complete message envelope
    created_at: datetime
    attempts: int
    next_attempt_at: datetime | None  # None on an unpublished row: exhausted
    correlation_id: UUID | None = None


class OutboxColumns:
    """The shared Tasks/Automation schema; each service maps it in its own metadata."""

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    type: Mapped[str] = mapped_column(Text)
    routing_key: Mapped[str] = mapped_column(Text)
    correlation_id: Mapped[UUID | None] = mapped_column(Uuid)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default=literal_column("0"))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    @declared_attr.directive
    @classmethod
    def __table_args__(cls) -> tuple[Index, Index]:
        return (
            Index(
                "outbox_unpublished",
                "next_attempt_at",
                postgresql_where=cls.published_at.is_(None),
            ),
            Index("outbox_correlation", "correlation_id"),
        )


async def enqueue(
    session: AsyncSession,
    table: Table,
    *,
    entry_id: UUID,
    type: str,
    routing_key: str,
    payload: dict[str, Any],
    now: datetime,
    correlation_id: UUID | None = None,
) -> bool:
    """Add a row in the caller's transaction; False when `entry_id` already exists."""
    result = await session.execute(
        insert(table)
        .values(
            id=entry_id,
            type=type,
            routing_key=routing_key,
            correlation_id=correlation_id,
            payload=payload,
            created_at=now,
            next_attempt_at=now,
        )
        .on_conflict_do_nothing(index_elements=[table.c.id])
        .returning(table.c.id)
    )
    return result.scalar_one_or_none() is not None


async def replay(
    session: AsyncSession, table: Table, *, correlation_id: UUID, now: datetime
) -> bool:
    """Make the unpublished row of `correlation_id` due again with a fresh budget."""
    result = await session.execute(
        update(table)
        .where(table.c.correlation_id == correlation_id, table.c.published_at.is_(None))
        .values(attempts=0, next_attempt_at=now)
        .returning(table.c.id)
    )
    return result.first() is not None


class OutboxTx(Protocol):
    async def claim_due(self, now: datetime, limit: int) -> list[OutboxRow]: ...

    async def mark_published(self, entry_id: UUID, now: datetime) -> None: ...

    async def reschedule(
        self,
        entry_id: UUID,
        attempts: int,
        next_attempt_at: datetime | None,
        error: str,
    ) -> None: ...


class SqlOutboxTx:
    """One relay's view of the table.

    `routing_keys` limits the claimed rows to the keys this relay can deliver: an
    exact key, or `prefix.*` for every key under that prefix. None claims all.
    """

    def __init__(
        self,
        session: AsyncSession,
        table: Table,
        routing_keys: Sequence[str] | None = None,
    ) -> None:
        self.session = session
        self._table = table
        self._routing_keys = routing_keys

    async def claim_due(self, now: datetime, limit: int) -> list[OutboxRow]:
        t = self._table
        rows = await self.session.execute(
            select(t)
            .where(
                t.c.published_at.is_(None),
                t.c.next_attempt_at <= now,
                *self._routed(),
            )
            .order_by(t.c.created_at, t.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [
            OutboxRow(
                id=row.id,
                type=row.type,
                routing_key=row.routing_key,
                payload=row.payload,
                created_at=row.created_at,
                attempts=row.attempts,
                next_attempt_at=row.next_attempt_at,
                correlation_id=row.correlation_id,
            )
            for row in rows
        ]

    async def mark_published(self, entry_id: UUID, now: datetime) -> None:
        t = self._table
        await self.session.execute(
            update(t).where(t.c.id == entry_id).values(published_at=now)
        )

    async def reschedule(
        self,
        entry_id: UUID,
        attempts: int,
        next_attempt_at: datetime | None,
        error: str,
    ) -> None:
        t = self._table
        await self.session.execute(
            update(t)
            .where(t.c.id == entry_id)
            .values(
                attempts=attempts, next_attempt_at=next_attempt_at, last_error=error
            )
        )

    def _routed(self) -> list[ColumnElement[bool]]:
        if self._routing_keys is None:
            return []
        column = self._table.c.routing_key
        return [
            or_(
                *(
                    column.startswith(key[:-1], autoescape=True)
                    if key.endswith(".*")
                    else column == key
                    for key in self._routing_keys
                )
            )
        ]


def sql_transactions(
    sessions: async_sessionmaker[AsyncSession],
    table: Table,
    *,
    routing_keys: Sequence[str] | None = None,
) -> Callable[[], AbstractAsyncContextManager[SqlOutboxTx]]:
    @asynccontextmanager
    async def transaction() -> AsyncIterator[SqlOutboxTx]:
        async with sessions() as session:
            yield SqlOutboxTx(session, table, routing_keys)
            await session.commit()  # an error above rolls the session back on close

    return transaction


class Publisher(Protocol):
    async def publish(
        self, routing_key: str, body: bytes, *, message_id: str, type: str
    ) -> None: ...


def is_broker_unavailable(error: BaseException) -> bool:
    """Connection loss and confirm timeouts: waiting for the broker is no attempt."""
    return isinstance(error, OSError | asyncio.TimeoutError | ChannelInvalidStateError)


def describe(error: BaseException) -> str:
    # Driver messages can carry credentials: only the exception class is stored.
    return f"publish failed: {type(error).__name__}"


class RelayOutbox[T: OutboxTx]:
    def __init__(
        self,
        transactions: Callable[[], AbstractAsyncContextManager[T]],
        publisher: Publisher,
        *,
        clock: Clock = utcnow,
        policy: RetryPolicy | None = None,
        on_exhausted: Callable[[T, OutboxRow, str], Awaitable[None]] | None = None,
        batch_size: int = 20,
    ) -> None:
        self._transactions = transactions
        self._publisher = publisher
        self._clock = clock
        self._policy = policy or RetryPolicy()
        self._on_exhausted = on_exhausted
        self._batch_size = batch_size

    async def execute(self) -> int:
        """Publish the due rows; return how many were confirmed."""
        now = self._clock()
        published = 0
        async with self._transactions() as tx:
            for row in await tx.claim_due(now, self._batch_size):
                try:
                    await self._publisher.publish(
                        row.routing_key,
                        json.dumps(row.payload, separators=(",", ":")).encode(),
                        message_id=str(row.id),
                        type=row.type,
                    )
                except Exception as error:
                    await self._failed(tx, row, error, now)
                else:
                    await tx.mark_published(row.id, now)
                    published += 1
        return published

    async def _failed(
        self, tx: T, row: OutboxRow, error: Exception, now: datetime
    ) -> None:
        message = describe(error)
        if is_broker_unavailable(error):
            delay = self._policy.delay_after(1)
            assert delay is not None
            await tx.reschedule(row.id, row.attempts, now + delay, message)
            return
        failed = row.attempts + 1
        delay = self._policy.delay_after(failed)
        await tx.reschedule(row.id, failed, now + delay if delay else None, message)
        if delay is None and self._on_exhausted is not None:
            await self._on_exhausted(tx, row, message)
