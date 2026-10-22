"""TaskCreated result: complete the job and store the author's notification."""

from dataclasses import dataclass
from uuid import UUID

from kernel_domain.result import Result

from application.ports import JobSummary, ResultOutcome, TaskResultStore
from domain.errors import AutomationErrors
from domain.jobs import result_intent_id_for
from domain.notifications import NotificationIntent

KIND = "task.result"
_PREVIEW = 100


@dataclass(frozen=True)
class RecordTaskResultCommand:
    message_id: str  # the broker message, for the inbox
    job_id: UUID
    task_id: UUID


class RecordTaskResultCommandHandler:
    def __init__(self, store: TaskResultStore) -> None:
        self._store = store

    async def execute(self, command: RecordTaskResultCommand) -> Result[bool]:
        """True when a new result was recorded; a storage failure propagates."""
        outcome = await self._store.record(
            command.message_id, command.job_id, command.task_id, _intent_for
        )
        if outcome is ResultOutcome.UNKNOWN_JOB:
            return Result[bool].fail(AutomationErrors.unknown_job())
        return Result[bool].ok(outcome is ResultOutcome.RECORDED)


def _result_text(text: str | None) -> str:
    content = text or ""
    preview = content if len(content) <= _PREVIEW else f"{content[:_PREVIEW]}…"
    return f"Задача создана: {preview}" if preview else "Задача создана"


def _intent_for(job: JobSummary) -> NotificationIntent:
    return NotificationIntent(
        id=result_intent_id_for(job.id),
        chat_id=job.chat_id,  # a private chat id equals the Telegram user id
        member_id=job.author_id,
        kind=KIND,
        access_version=0,  # a result is not tied to an access decision
        body=_result_text(job.text),
    )
