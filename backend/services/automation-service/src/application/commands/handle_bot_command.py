"""Login and join commands have no task-creation side effects."""

from dataclasses import dataclass

from kernel_domain.errors import ErrorType

from application.ports import (
    IdentityGateway,
    IdentityUnavailable,
    Messenger,
    UpdateInbox,
)
from domain.value_objects.join_outcome import JoinOutcome

_DENIED = "Доступ не предоставлен. Обратитесь к администратору."
_JOIN_REPLIES = {
    JoinOutcome.SUBMITTED: (
        "Заявка отправлена. Администратор рассмотрит её, мы сообщим о решении."
    ),
    JoinOutcome.ALREADY_PENDING: "Заявка уже на рассмотрении.",
    JoinOutcome.ALREADY_MEMBER: "Вы уже участник. Для входа отправьте /login.",
    JoinOutcome.BLOCKED: "Доступ заблокирован. Обратитесь к администратору.",
}
_START_HINTS = {
    "pending": "Заявка на рассмотрении.",
    "rejected": "Заявка отклонена. Подать её снова можно командой /join.",
}


@dataclass(frozen=True, slots=True)
class HandleBotCommandDto:
    """Параметры входящего события от Telegram-бота."""

    update_id: int
    telegram_id: int
    chat_id: int
    text: str
    private: bool
    display_name: str | None = None


class HandleBotCommand:
    def __init__(
        self, identity: IdentityGateway, messenger: Messenger, inbox: UpdateInbox
    ) -> None:
        self._identity = identity
        self._messenger = messenger
        self._inbox = inbox

    async def execute(self, command: HandleBotCommandDto) -> None:
        command_name = (
            command.text.split(maxsplit=1)[0].split("@", 1)[0] if command.text else ""
        )
        if not command.private or command_name not in {"/start", "/login", "/join"}:
            return
        if not await self._inbox.claim(command.update_id):
            return
        if command_name == "/join":
            reply = await self._join(command.telegram_id, command.display_name)
        else:
            reply = await self._login(command_name, command.telegram_id)
        await self._messenger.send(command.chat_id, reply)

    async def _join(self, telegram_id: int, display_name: str | None) -> str:
        # Identity alone decides; the bot only relays the outcome.
        try:
            joined = await self._identity.submit_join_request(telegram_id, display_name)
        except IdentityUnavailable:
            return "Заявки временно недоступны. Повторите /join позже."
        if joined.is_err:
            if joined.error.type is ErrorType.RATE_LIMITED:
                return "Слишком много запросов. Повторите /join через минуту."
            return "Заявки временно недоступны. Повторите /join позже."
        return _JOIN_REPLIES[joined.value]

    async def _login(self, command_name: str, telegram_id: int) -> str:
        try:
            member = await self._identity.find_member(telegram_id)
            if command_name == "/start":
                if member is None:
                    return "Чтобы запросить доступ, отправьте /join."
                if member.status in _START_HINTS:
                    return _START_HINTS[member.status]
            if member is None or member.status != "approved":
                return _DENIED
            if command_name == "/start":
                return "Для входа в веб-панель отправьте /login."
            issued = await self._identity.issue_login_code(telegram_id)
        except IdentityUnavailable:
            return "Вход временно недоступен. Повторите /login позже."
        if issued.is_err:
            if issued.error.type is ErrorType.RATE_LIMITED:
                return "Слишком много запросов. Повторите /login через минуту."
            return _DENIED
        return f"Код входа: {issued.value}\nДействует 5 минут, только один раз."
