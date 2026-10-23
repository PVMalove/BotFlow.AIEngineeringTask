"""Operator replay of one failed job, with the original job and outbox row ids."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from kernel_domain.result import Result
from kernel_platform.clock import utcnow

from domain.errors import AutomationErrors
from domain.job_limits import JobLimits
from domain.unit_of_work import AutomationUnitOfWork
from domain.value_objects.job_id import JobId
from domain.value_objects.job_status import JobKind


class ReplayOutcome(StrEnum):
    REPLAYED = "replayed"
    UNCHANGED = "unchanged"  # the job did not fail (or was already replayed)


@dataclass(frozen=True)
class ReplayJobCommand:
    job_id: UUID


class ReplayJobCommandHandler:
    def __init__(
        self,
        uow_factory: Callable[[], AutomationUnitOfWork],
        limits: JobLimits = JobLimits(),
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._uow_factory = uow_factory
        self._limits = limits
        self._clock = clock

    async def execute(self, command: ReplayJobCommand) -> Result[ReplayOutcome]:
        """One transaction: acceptance lock, job lock, limits, restart.

        A failed job holds no slot, so it needs one again like a new job. Only a
        failed job is restarted: a repeated command finds it accepted and changes
        nothing, and a refusal leaves the job failed.
        """
        async with self._uow_factory() as uow:
            await uow.jobs.lock_acceptance()
            job = await uow.jobs.get(JobId(command.job_id), lock=True)
            if job is None:
                return Result[ReplayOutcome].fail(AutomationErrors.unknown_job())
            if not job.has_failed:
                return Result[ReplayOutcome].ok(ReplayOutcome.UNCHANGED)
            counts = await uow.jobs.count_unfinished(job.author_id)
            refusal = self._limits.check(counts, voice=job.kind is JobKind.VOICE)
            if refusal is not None:
                return Result[ReplayOutcome].fail(refusal)
            now = self._clock()
            job.restart_voice(now) or job.restart_delivery(now)
            await uow.jobs.update(job)
            await uow.commit()
        return Result[ReplayOutcome].ok(ReplayOutcome.REPLAYED)
