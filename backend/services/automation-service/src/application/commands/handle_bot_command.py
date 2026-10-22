"""Login commands have no task-creation side effects."""

from dataclasses import dataclass

from application.ports import (
    IdentityGateway,
    IdentityUnavailable,
    LoginDenied,
    LoginRateLimited,
    Messenger,
    UpdateInbox,
)


@dataclass(frozen=True, slots=True)
class HandleBotCommandDto:
    """Параметры входящего события от Telegram-бота."""

    update_id: int
    telegram_id: int
    chat_id: int
    text: str
    private: bool


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
    ) -> None:
        if command is not None:
            update_id = command.update_id
            telegram_id = command.telegram_id
            chat_id = command.chat_id
            text = command.text
            private = command.private

        command_name = text.split(maxsplit=1)[0].split("@", 1)[0] if text else ""
        if not private or command_name not in {"/start", "/login"}:
            return
        if not await self._inbox.claim(update_id):
            return
        try:
            member = await self._identity.find_member(telegram_id)
            if member is None or member.status != "approved":
                raise LoginDenied
            if command_name == "/start":
                reply = "Для входа в веб-панель отправьте /login."
            else:
                code = await self._identity.issue_login_code(telegram_id)
                reply = f"Код входа: {code}\nДействует 5 минут, только один раз."
        except LoginDenied:
            reply = "Доступ не предоставлен. Обратитесь к администратору."
        except LoginRateLimited:
            reply = "Слишком много запросов. Повторите /login через минуту."
        except IdentityUnavailable:
            reply = "Вход временно недоступен. Повторите /login позже."
        await self._messenger.send(chat_id, reply)


__all__ = ["HandleBotCommand", "HandleBotCommandDto"]
