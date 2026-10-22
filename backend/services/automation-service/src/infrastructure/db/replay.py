"""Manual recovery: run a failed job again (operator commands of the worker)."""

from datetime import UTC, datetime
from uuid import UUID

from kernel_platform.outbox import replay
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.value_objects.job_id import JobId
from infrastructure.db.job_repository import SqlJobRepository
from infrastructure.db.outbox_events import stage_automation_event
from infrastructure.db.tables import outbox


async def replay_job(sessions: async_sessionmaker[AsyncSession], job_id: UUID) -> bool:
    """Make the unpublished CreateTask of a failed job due again; False if none."""
    now = datetime.now(UTC)
    async with sessions() as session:
        if not await replay(session, outbox, correlation_id=job_id, now=now):
            return False
        repository = SqlJobRepository(session, lambda _: None)
        job = await repository.get(JobId(job_id), lock=True)
        if job is not None and job.restart_delivery(now):
            await repository.update(job)
        await session.commit()
    return True


async def replay_voice_job(
    sessions: async_sessionmaker[AsyncSession], job_id: UUID
) -> bool:
    """Run a failed voice job again from the stored object, or from Telegram."""
    now = datetime.now(UTC)
    async with sessions() as session:
        repository = SqlJobRepository(session, lambda _: None)
        job = await repository.get(JobId(job_id), lock=True)
        if job is None or not job.restart_voice(now):
            return False
        await repository.update(job)
        for event in job.pull_events():
            await stage_automation_event(session, event)
        await session.commit()
    return True
