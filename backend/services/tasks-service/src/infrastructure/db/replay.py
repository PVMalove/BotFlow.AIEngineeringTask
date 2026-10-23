from datetime import UTC, datetime
from uuid import UUID

from kernel_platform.outbox import replay
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infrastructure.db.models import OutboxModel


async def replay_task_created(
    sessions: async_sessionmaker[AsyncSession], job_id: UUID
) -> bool:
    """Make the unpublished TaskCreated of a job due again; False if none."""
    async with sessions() as session:
        replayed = await replay(
            session, OutboxModel, correlation_id=job_id, now=datetime.now(UTC)
        )
        if replayed:
            await session.commit()
        return replayed
