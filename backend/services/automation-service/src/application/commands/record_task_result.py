"""TaskCreated result: complete the job; its event stores the author's notification."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from kernel_domain.result import Result
from kernel_platform.clock import utcnow

from domain.errors import AutomationErrors
from domain.unit_of_work import AutomationUnitOfWork
from domain.value_objects.job_id import JobId


@dataclass(frozen=True)
class RecordTaskResultCommand:
    message_id: str  # the broker message, for the inbox
    job_id: UUID
    task_id: UUID


class RecordTaskResultCommandHandler:
    def __init__(
        self,
        uow_factory: Callable[[], AutomationUnitOfWork],
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: RecordTaskResultCommand) -> Result[bool]:
        """True when the message was new; a storage failure propagates."""
        async with self._uow_factory() as uow:
            job = await uow.jobs.get(JobId(command.job_id), lock=True)
            if job is None:
                return Result[bool].fail(AutomationErrors.unknown_job())
            if not await uow.inbox.claim(command.message_id):
                return Result[bool].ok(False)  # redelivery: already recorded
            # A job that failed after its retries completes as well: a late result wins.
            if job.record_result(command.task_id, self._clock()):
                await uow.jobs.update(job)
            await uow.commit()  # the broker message is acknowledged only after this
        return Result[bool].ok(True)
