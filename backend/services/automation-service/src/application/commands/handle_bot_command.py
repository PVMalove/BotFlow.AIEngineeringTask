"""Login and join commands have no task-creation side effects."""

from dataclasses import dataclass

from application.ports import (
    IdentityGateway,
    IdentityUnavailable,
    JoinOutcome,
    LoginDenied,
    LoginRateLimited,
    Messenger,
    UpdateInbox,
)

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

    async def execute(
        self,
        command: HandleBotCommandDto | None = None,
        *,
        update_id: int = 0,
        telegram_id: int = 0,
        chat_id: int = 0,
        text: str = "",
        private: bool = False,
        display_name: str | None = None,
    ) -> None:
        if command is not None:
            update_id = command.update_id
            telegram_id = command.telegram_id
            chat_id = command.chat_id
            text = command.text
            private = command.private
            display_name = command.display_name

        command_name = text.split(maxsplit=1)[0].split("@", 1)[0] if text else ""
        if not private or command_name not in {"/start", "/login", "/join"}:
            return
        if not await self._inbox.claim(update_id):
            return
        if command_name == "/join":
            reply = await self._join(telegram_id, display_name)
        else:
            reply = await self._login(command_name, telegram_id)
        await self._messenger.send(chat_id, reply)

    async def _join(self, telegram_id: int, display_name: str | None) -> str:
        # Identity alone decides; the bot only relays the outcome.
        try:
            outcome = await self._identity.submit_join_request(
                telegram_id, display_name
            )
        except LoginRateLimited:
            return "Слишком много запросов. Повторите /join через минуту."
        except (IdentityUnavailable, LoginDenied):
            return "Заявки временно недоступны. Повторите /join позже."
        return _JOIN_REPLIES[outcome]

    async def _login(self, command_name: str, telegram_id: int) -> str:
        try:
            member = await self._identity.find_member(telegram_id)
            if command_name == "/start":
                if member is None:
                    return "Чтобы запросить доступ, отправьте /join."
                if member.status in _START_HINTS:
                    return _START_HINTS[member.status]
            if member is None or member.status != "approved":
                raise LoginDenied
            if command_name == "/start":
                return "Для входа в веб-панель отправьте /login."
            code = await self._identity.issue_login_code(telegram_id)
            return f"Код входа: {code}\nДействует 5 минут, только один раз."
        except LoginDenied:
            return _DENIED
        except LoginRateLimited:
            return "Слишком много запросов. Повторите /login через минуту."
        except IdentityUnavailable:
            return "Вход временно недоступен. Повторите /login позже."


__all__ = ["HandleBotCommand", "HandleBotCommandDto"]
