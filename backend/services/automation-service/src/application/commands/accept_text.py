"""A text message from an admitted author becomes a stored job; ACK follows commit."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from botflow_platform.messaging import TASK_CREATE_ROUTING_KEY

from application.commands.admission import Admission
from application.ports import (
    Accepted,
    AcceptOutcome,
    JobStore,
    MemberDirectory,
    Messenger,
    NewJob,
    OutboxMessage,
)
from domain.jobs import job_id_for, outbox_id_for

COMMAND_TYPE = "CreateTask"
ENVELOPE_VERSION = 1
ACK = "Принято. Создаю задачу, сообщу, когда она будет готова."
LIMIT_REPLY = "Слишком много задач в обработке. Повторите отправку позже."


@dataclass(frozen=True, slots=True)
class AcceptTextDto:
    bot_id: int
    chat_id: int
    message_id: int
    telegram_id: int
    text: str
    display_name: str
    message_date: datetime
    received_at: datetime


class AcceptTextMessage:
    def __init__(
        self,
        identity: MemberDirectory,
        jobs: JobStore,
        messenger: Messenger,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._admission = Admission(identity, messenger)
        self._jobs = jobs
        self._clock = clock

    async def execute(self, command: AcceptTextDto) -> None:
        """Storage failures propagate: the update is then not acknowledged."""
        author_id = await self._admission.admit(command.telegram_id, command.chat_id)
        if author_id is None:
            return
        job_id = job_id_for(command.bot_id, command.chat_id, command.message_id)
        job = NewJob(
            id=job_id,
            bot_id=command.bot_id,
            chat_id=command.chat_id,
            message_id=command.message_id,
            author_id=author_id,
            author_telegram_id=command.telegram_id,
            author_name=command.display_name,
            text=command.text,
            message_date=command.message_date,
            received_at=command.received_at,
        )
        accepted = await self._jobs.accept(job, self._create_task(job))
        if accepted.outcome is AcceptOutcome.LIMIT:
            await self._admission.reply(command.chat_id, LIMIT_REPLY)
        elif not accepted.acknowledged:
            await self._acknowledge(command.chat_id, accepted)

    async def _acknowledge(self, chat_id: int, accepted: Accepted) -> None:
        # Only now, after the commit, does the author hear that the text is safe.
        if await self._admission.reply(chat_id, ACK):
            await self._jobs.mark_acked(accepted.job_id, self._clock())

    def _create_task(self, job: NewJob) -> OutboxMessage:
        message_id = outbox_id_for(job.id)
        payload: dict[str, Any] = {
            "job_id": str(job.id),
            "bot_id": job.bot_id,
            "chat_id": job.chat_id,
            "message_id": job.message_id,
            "author_id": str(job.author_id),
            "author_telegram_id": job.author_telegram_id,
            "author_name": job.author_name,
            "text": job.text,
        }
        return OutboxMessage(
            id=message_id,
            correlation_id=job.id,
            type=COMMAND_TYPE,
            routing_key=TASK_CREATE_ROUTING_KEY,
            payload={
                "envelope_version": ENVELOPE_VERSION,
                "message_id": str(message_id),
                "type": COMMAND_TYPE,
                "correlation_id": str(job.id),
                "occurred_at": job.received_at.isoformat(),
                "payload": payload,
            },
        )
