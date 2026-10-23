"""What happens to a job when the hand-off of its outbox row is exhausted."""

from datetime import UTC, datetime

from kernel_platform.outbox import OutboxRow, SqlOutboxTx
from sqlalchemy.ext.asyncio import AsyncSession

from domain.entities.job import Job
from domain.notices import VOICE_FAILURES
from domain.value_objects.job_id import JobId
from infrastructure.db.job_repository import SqlJobRepository
from infrastructure.db.outbox_events import stage_automation_event


async def fail_job(tx: SqlOutboxTx, row: OutboxRow, error: str) -> None:
    """Retries are exhausted: the job fails in the same transaction (slot freed)."""
    job = await _locked_job(tx.session, row)
    if job is not None and job.fail_delivery(error, datetime.now(UTC)):
        await SqlJobRepository(tx.session, lambda _: None).update(job)


async def fail_voice_job(tx: SqlOutboxTx, row: OutboxRow, error: str) -> None:
    """The hand-off to the STT stream is exhausted: end the job like a final error."""
    job = await _locked_job(tx.session, row)
    if job is None:
        return
    failed = job.fail_voice(
        "unavailable", VOICE_FAILURES["unavailable"], datetime.now(UTC)
    )
    if failed:
        await SqlJobRepository(tx.session, lambda _: None).update(job)
        for event in job.pull_events():
            await stage_automation_event(tx.session, event)


async def _locked_job(session: AsyncSession, row: OutboxRow) -> Job | None:
    if row.correlation_id is None:
        return None
    return await SqlJobRepository(session, lambda _: None).get(
        JobId(row.correlation_id), lock=True
    )
