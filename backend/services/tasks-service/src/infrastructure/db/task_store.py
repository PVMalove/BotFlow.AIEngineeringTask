"""Inbox, task and result outbox are written in one transaction."""

from datetime import UTC, datetime
from uuid import UUID

from botflow_platform.outbox import enqueue, replay
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from application.ports import NewTask, TaskCreatedMessage
from domain.tasks import TaskStatus
from infrastructure.db.tables import inbox_messages, outbox, tasks


class SqlTaskCommandStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(
        self, message_id: str, task: NewTask, created: TaskCreatedMessage
    ) -> bool:
        now = datetime.now(UTC)
        async with self._sessions() as session:
            claimed = await session.execute(
                insert(inbox_messages)
                .values(message_id=message_id, received_at=now)
                .on_conflict_do_nothing(index_elements=[inbox_messages.c.message_id])
                .returning(inbox_messages.c.message_id)
            )
            if claimed.scalar_one_or_none() is None:
                return False  # redelivery: the first copy already did everything
            # The unique source makes a second message for it a no-op; the task id
            # and the result id are derived from the source, so both stay one row.
            await session.execute(
                insert(tasks)
                .values(
                    id=task.id,
                    source_key=task.source_key,
                    correlation_id=task.correlation_id,
                    text=task.text,
                    status=TaskStatus.PENDING.value,
                    version=1,
                    author_id=task.author_id,
                    author_telegram_id=task.author_telegram_id,
                    author_name=task.author_name,
                    created_at=task.created_at,
                    updated_at=task.created_at,
                )
                .on_conflict_do_nothing()
            )
            await enqueue(
                session,
                outbox,
                entry_id=created.id,
                type=created.type,
                routing_key=created.routing_key,
                payload=created.payload,
                correlation_id=created.correlation_id,
                now=now,
            )
            await session.commit()  # the broker message is acknowledged only after this
        return True


async def replay_task_created(
    sessions: async_sessionmaker[AsyncSession], job_id: UUID
) -> bool:
    """Make the unpublished TaskCreated of a job due again; False if none."""
    now = datetime.now(UTC)
    async with sessions() as session:
        replayed = await replay(session, outbox, correlation_id=job_id, now=now)
        if replayed:
            await session.commit()
        return replayed
