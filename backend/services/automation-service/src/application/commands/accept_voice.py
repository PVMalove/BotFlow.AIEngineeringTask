"""A voice message from an admitted author becomes a stored job; ACK follows commit."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from application.commands.admission import Admission
from application.commands.job_acceptance import Accepted, JobAcceptance
from application.ports import MemberDirectory, Messenger
from domain.entities.job import Job
from domain.entities.voice_message import VoiceMessage
from domain.unit_of_work import AutomationUnitOfWork

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
class AcceptVoiceCommand:
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


class AcceptVoiceCommandHandler:
    def __init__(
        self,
        identity: MemberDirectory,
        uow_factory: Callable[[], AutomationUnitOfWork],
        messenger: Messenger,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._admission = Admission(identity, messenger)
        self._acceptance = JobAcceptance(uow_factory, clock=clock)
        self._clock = clock

    async def execute(self, command: AcceptVoiceCommand) -> None:
        """Storage failures propagate: the update is then not acknowledged."""
        author_id = await self._admission.admit(command.telegram_id, command.chat_id)
        if author_id is None:
            return
        refusal = _invalid(command)
        if refusal is not None:
            await self._admission.reply(command.chat_id, refusal)
            return
        job = Job.accept_voice(
            VoiceMessage(
                file_id=command.file_id,
                file_unique_id=command.file_unique_id,
                duration_seconds=command.duration_seconds,
                file_size_bytes=command.file_size_bytes,
                mime_type=command.mime_type,
            ),
            bot_id=command.bot_id,
            chat_id=command.chat_id,
            message_id=command.message_id,
            author_id=author_id,
            author_telegram_id=command.telegram_id,
            author_name=command.display_name,
            message_date=command.message_date,
            received_at=command.received_at,
            now=self._clock(),
        )
        accepted = await self._acceptance.accept(job)
        if accepted.is_err:
            reply = (
                VOICE_LIMIT_REPLY
                if accepted.error.code == "voice_limit"
                else LIMIT_REPLY
            )
            await self._admission.reply(command.chat_id, reply)
        elif not accepted.value.acknowledged:
            await self._acknowledge(command.chat_id, accepted.value)

    async def _acknowledge(self, chat_id: int, accepted: Accepted) -> None:
        # Only now, after the commit, does the author hear that the voice is safe.
        if await self._admission.reply(chat_id, ACK):
            await self._acceptance.acknowledge(accepted.job_id)


def _invalid(command: AcceptVoiceCommand) -> str | None:
    if command.duration_seconds > MAX_DURATION_SECONDS:
        return TOO_LONG
    if command.file_size_bytes is not None and command.file_size_bytes > MAX_FILE_BYTES:
        return TOO_LARGE
    return None
