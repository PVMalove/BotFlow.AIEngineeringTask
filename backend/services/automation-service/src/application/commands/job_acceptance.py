"""Accepting a job: the duplicate check, the limits and the ACK, for text and voice."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from kernel_domain.result import Result

from domain.entities.job import Job
from domain.job_limits import JobLimits
from domain.unit_of_work import AutomationUnitOfWork
from domain.value_objects.job_id import JobId


class AcceptOutcome(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"  # the same bot/chat/message was already accepted


@dataclass(frozen=True, slots=True)
class Accepted:
    outcome: AcceptOutcome
    job_id: JobId
    acknowledged: bool  # a positive ACK was already recorded for this job


class JobAcceptance:
    def __init__(
        self,
        uow_factory: Callable[[], AutomationUnitOfWork],
        limits: JobLimits = JobLimits(),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._uow_factory = uow_factory
        self._limits = limits
        self._clock = clock

    async def accept(self, job: Job) -> Result[Accepted]:
        """Duplicate check, limits, job and its outbox row in ONE transaction; a
        limit is an error and nothing is stored."""
        async with self._uow_factory() as uow:
            await uow.jobs.lock_acceptance()
            existing = await uow.jobs.get_by_source(
                job.bot_id, job.chat_id, job.message_id
            )
            if existing is not None:  # checked before the limits: a retry is no load
                return Result[Accepted].ok(
                    Accepted(
                        AcceptOutcome.DUPLICATE,
                        existing.id,
                        existing.acked_at is not None,
                    )
                )
            counts = await uow.jobs.count_unfinished(job.author_id)
            refusal = self._limits.check(counts, voice=job.voice is not None)
            if refusal is not None:
                return Result[Accepted].fail(refusal)
            await uow.jobs.add(job)
            await uow.commit()  # the ACK is sent only after this
        return Result[Accepted].ok(Accepted(AcceptOutcome.ACCEPTED, job.id, False))

    async def acknowledge(self, job_id: JobId) -> None:
        """Record that the author heard it; the first ACK wins."""
        async with self._uow_factory() as uow:
            job = await uow.jobs.get(job_id, lock=True)
            if job is not None and job.acked_at is None:
                job.mark_acked(self._clock())
                await uow.jobs.update(job)
                await uow.commit()
