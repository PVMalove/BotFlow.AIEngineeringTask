"""Result of a task and the failure of a job, each in one transaction."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from botflow_platform.outbox import OutboxRow, SqlOutboxTx, replay
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from application.ports import JobSummary, ResultOutcome
from domain.jobs import JobStatus
from domain.notifications import NotificationIntent
from infrastructure.db.jobs import jobs, outbox
from infrastructure.db.notifications import inbox_messages, notification_intents


class SqlTaskResultStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record(
        self,
        message_id: str,
        job_id: UUID,
        task_id: UUID,
        intent_for: Callable[[JobSummary], NotificationIntent],
    ) -> ResultOutcome:
        now = datetime.now(UTC)
        async with self._sessions() as session:
            job = (
                await session.execute(
                    select(jobs.c.chat_id, jobs.c.author_id, jobs.c.text, jobs.c.status)
                    .where(jobs.c.id == job_id)
                    .with_for_update()
                )
            ).one_or_none()
            if job is None:
                return ResultOutcome.UNKNOWN_JOB
            claimed = await session.execute(
                insert(inbox_messages)
                .values(message_id=message_id, received_at=now)
                .on_conflict_do_nothing(index_elements=[inbox_messages.c.message_id])
                .returning(inbox_messages.c.message_id)
            )
            if claimed.scalar_one_or_none() is None:
                return ResultOutcome.DUPLICATE  # redelivery: already recorded
            # A job that failed after its retries completes as well: a late result wins.
            completed = await session.execute(
                update(jobs)
                .where(
                    jobs.c.id == job_id,
                    jobs.c.status.in_(
                        (JobStatus.ACCEPTED.value, JobStatus.FAILED.value)
                    ),
                )
                .values(
                    status=JobStatus.COMPLETED.value,
                    task_id=task_id,
                    last_error=None,
                    updated_at=now,
                )
                .returning(jobs.c.id)
            )
            if completed.first() is not None:
                intent = intent_for(
                    JobSummary(job_id, job.chat_id, job.author_id, job.text)
                )
                await session.execute(
                    insert(notification_intents)
                    .values(
                        id=intent.id,
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
                )
            await session.commit()  # the broker message is acknowledged only after this
        return ResultOutcome.RECORDED


async def fail_job(tx: SqlOutboxTx, row: OutboxRow, error: str) -> None:
    """Retries are exhausted: the job fails in the same transaction (slot freed)."""
    await tx.session.execute(
        update(jobs)
        .where(jobs.c.id == row.correlation_id, jobs.c.status == "accepted")
        .values(
            status=JobStatus.FAILED.value,
            last_error=error,
            updated_at=datetime.now(UTC),
        )
    )


async def replay_job(sessions: async_sessionmaker[AsyncSession], job_id: UUID) -> bool:
    """Make the unpublished CreateTask of a failed job due again; False if none."""
    now = datetime.now(UTC)
    async with sessions() as session:
        if not await replay(session, outbox, correlation_id=job_id, now=now):
            return False
        await session.execute(
            update(jobs)
            .where(jobs.c.id == job_id, jobs.c.status == JobStatus.FAILED.value)
            .values(status=JobStatus.ACCEPTED.value, updated_at=now)
        )
        await session.commit()
    return True
