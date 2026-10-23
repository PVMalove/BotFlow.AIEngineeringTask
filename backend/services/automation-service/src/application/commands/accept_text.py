"""A text message from an admitted author becomes a stored job; ACK follows commit."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from kernel_platform.clock import utcnow

from application.commands.admission import Admission
from application.commands.job_acceptance import Accepted, JobAcceptance
from application.ports import MemberDirectory, Messenger
from domain.entities.job import Job
from domain.unit_of_work import AutomationUnitOfWork

ACK = "Принято. Создаю задачу, сообщу, когда она будет готова."
LIMIT_REPLY = "Слишком много задач в обработке. Повторите отправку позже."


@dataclass(frozen=True, slots=True)
class AcceptTextCommand:
    bot_id: int
    chat_id: int
    message_id: int
    telegram_id: int
    text: str
    display_name: str
    message_date: datetime
    received_at: datetime


class AcceptTextCommandHandler:
    def __init__(
        self,
        identity: MemberDirectory,
        uow_factory: Callable[[], AutomationUnitOfWork],
        messenger: Messenger,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._admission = Admission(identity, messenger)
        self._acceptance = JobAcceptance(uow_factory, clock=clock)
        self._clock = clock

    async def execute(self, command: AcceptTextCommand) -> None:
        """Storage failures propagate: the update is then not acknowledged."""
        author_id = await self._admission.admit(command.telegram_id, command.chat_id)
        if author_id is None:
            return
        job = Job.accept_text(
            bot_id=command.bot_id,
            chat_id=command.chat_id,
            message_id=command.message_id,
            author_id=author_id,
            author_telegram_id=command.telegram_id,
            author_name=command.display_name,
            text=command.text,
            message_date=command.message_date,
            received_at=command.received_at,
            now=self._clock(),
        )
        accepted = await self._acceptance.accept(job)
        if accepted.is_err:
            await self._admission.reply(command.chat_id, LIMIT_REPLY)
        elif not accepted.value.acknowledged:
            await self._acknowledge(command.chat_id, accepted.value)

    async def _acknowledge(self, chat_id: int, accepted: Accepted) -> None:
        # Only now, after the commit, does the author hear that the text is safe.
        if await self._admission.reply(chat_id, ACK):
            await self._acceptance.acknowledge(accepted.job_id)
