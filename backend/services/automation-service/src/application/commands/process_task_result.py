"""TaskCreated result: complete the job and store the author's notification."""

import json
from uuid import UUID

from botflow_platform.consumer import InvalidMessage

from application.ports import JobSummary, ResultOutcome, TaskResultStore
from domain.jobs import result_intent_id_for
from domain.notifications import NotificationIntent

RESULT_TYPE = "TaskCreated"
ENVELOPE_VERSION = 1
KIND = "task.result"
_PREVIEW = 100


def _result_text(text: str) -> str:
    preview = text if len(text) <= _PREVIEW else f"{text[:_PREVIEW]}…"
    return f"Задача создана: {preview}"


class ProcessTaskResult:
    def __init__(self, store: TaskResultStore) -> None:
        self._store = store

    async def execute(
        self, message_id: str | None, message_type: str | None, body: bytes
    ) -> bool:
        """True when a new result was recorded; a storage failure propagates."""
        if message_id is None or message_type != RESULT_TYPE:
            raise InvalidMessage
        job_id, task_id = self._parse(body)
        outcome = await self._store.record(message_id, job_id, task_id, _intent_for)
        if outcome is ResultOutcome.UNKNOWN_JOB:
            raise InvalidMessage
        return outcome is ResultOutcome.RECORDED

    async def handle(
        self, message_id: str | None, message_type: str | None, body: bytes
    ) -> None:
        """Queue-consumer entry point."""
        await self.execute(message_id, message_type, body)

    @staticmethod
    def _parse(body: bytes) -> tuple[UUID, UUID]:
        try:
            envelope = json.loads(body)
            if envelope["envelope_version"] != ENVELOPE_VERSION:
                raise ValueError
            payload = envelope["payload"]
            return UUID(payload["job_id"]), UUID(payload["task_id"])
        except (ValueError, KeyError, TypeError, AttributeError):
            raise InvalidMessage from None


def _intent_for(job: JobSummary) -> NotificationIntent:
    return NotificationIntent(
        id=result_intent_id_for(job.id),
        chat_id=job.chat_id,  # a private chat id equals the Telegram user id
        member_id=job.author_id,
        kind=KIND,
        access_version=0,  # a result is not tied to an access decision
        body=_result_text(job.text),
    )
