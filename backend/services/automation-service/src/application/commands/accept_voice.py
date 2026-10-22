"""A voice message from an admitted author becomes a stored job; ACK follows commit."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from application.commands.admission import Admission
from application.ports import (
    Accepted,
    AcceptOutcome,
    MemberDirectory,
    Messenger,
    NewVoiceJob,
    OutboxMessage,
    VoiceJobStore,
)
from domain.jobs import job_id_for

# Distinct from `outbox_id_for`: the CreateTask row of the same job comes later.
_TRANSCRIBE_NAMESPACE = UUID("2c9d6f4e-5a0b-4b57-8e57-0e6f1b3a7d04")

COMMAND_TYPE = "TranscribeVoice"
VOICE_ROUTING_KEY = "voice.transcribe"
ENVELOPE_VERSION = 1
MAX_DURATION_SECONDS = 60
MAX_FILE_BYTES = 5 * 1024 * 1024
ACK = (
    "Принято. Распознаю голосовое сообщение и создам задачу; если очередь занята, "
    "придётся немного подождать. Сообщу, когда задача будет готова."
)
TOO_LONG = "Голосовое сообщение длиннее 60 секунд. Отправьте более короткое."
TOO_LARGE = "Голосовое сообщение больше 5 МиБ. Отправьте более короткое."
LIMIT_REPLY = "Слишком много задач в обработке. Повторите отправку позже."
VOICE_LIMIT_REPLY = (
    "Слишком много голосовых сообщений в обработке. Дождитесь результата и "
    "повторите отправку."
)


@dataclass(frozen=True, slots=True)
class AcceptVoiceDto:
    bot_id: int
    chat_id: int
    message_id: int
    telegram_id: int
    file_id: str
    file_unique_id: str
    duration_seconds: int
    file_size_bytes: int | None  # Telegram may omit it
    mime_type: str | None
    display_name: str
    message_date: datetime
    received_at: datetime


class AcceptVoiceMessage:
    def __init__(
        self,
        identity: MemberDirectory,
        jobs: VoiceJobStore,
        messenger: Messenger,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._admission = Admission(identity, messenger)
        self._jobs = jobs
        self._clock = clock

    async def execute(self, command: AcceptVoiceDto) -> None:
        """Storage failures propagate: the update is then not acknowledged."""
        author_id = await self._admission.admit(command.telegram_id, command.chat_id)
        if author_id is None:
            return
        refusal = _invalid(command)
        if refusal is not None:
            await self._admission.reply(command.chat_id, refusal)
            return
        job = NewVoiceJob(
            id=job_id_for(command.bot_id, command.chat_id, command.message_id),
            bot_id=command.bot_id,
            chat_id=command.chat_id,
            message_id=command.message_id,
            author_id=author_id,
            author_telegram_id=command.telegram_id,
            author_name=command.display_name,
            file_id=command.file_id,
            file_unique_id=command.file_unique_id,
            duration_seconds=command.duration_seconds,
            file_size_bytes=command.file_size_bytes,
            mime_type=command.mime_type,
            message_date=command.message_date,
            received_at=command.received_at,
        )
        accepted = await self._jobs.accept_voice(job, _transcribe(job))
        if accepted.outcome is AcceptOutcome.LIMIT:
            await self._admission.reply(command.chat_id, LIMIT_REPLY)
        elif accepted.outcome is AcceptOutcome.VOICE_LIMIT:
            await self._admission.reply(command.chat_id, VOICE_LIMIT_REPLY)
        elif not accepted.acknowledged:
            await self._acknowledge(command.chat_id, accepted)

    async def _acknowledge(self, chat_id: int, accepted: Accepted) -> None:
        # Only now, after the commit, does the author hear that the voice is safe.
        if await self._admission.reply(chat_id, ACK):
            await self._jobs.mark_acked(accepted.job_id, self._clock())


def transcribe_outbox_id_for(job_id: UUID) -> UUID:
    return uuid5(_TRANSCRIBE_NAMESPACE, str(job_id))


def _invalid(command: AcceptVoiceDto) -> str | None:
    if command.duration_seconds > MAX_DURATION_SECONDS:
        return TOO_LONG
    if command.file_size_bytes is not None and command.file_size_bytes > MAX_FILE_BYTES:
        return TOO_LARGE
    return None


def _transcribe(job: NewVoiceJob) -> OutboxMessage:
    """The audio is addressed through the job's voice record, never by a URL."""
    message_id = transcribe_outbox_id_for(job.id)
    payload: dict[str, Any] = {"job_id": str(job.id)}
    return OutboxMessage(
        id=message_id,
        correlation_id=job.id,
        type=COMMAND_TYPE,
        routing_key=VOICE_ROUTING_KEY,
        payload={
            "envelope_version": ENVELOPE_VERSION,
            "message_id": str(message_id),
            "type": COMMAND_TYPE,
            "correlation_id": str(job.id),
            "occurred_at": job.received_at.isoformat(),
            "payload": payload,
        },
    )
