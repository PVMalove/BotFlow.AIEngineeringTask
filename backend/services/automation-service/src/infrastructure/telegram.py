"""aiogram adapter; login codes are sent only through the Messenger port."""

from datetime import UTC, datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, Update, User

from application.commands.accept_text import AcceptTextDto, AcceptTextMessage
from application.commands.handle_bot_command import (
    HandleBotCommand,
    HandleBotCommandDto,
)
from application.ports import Messenger

UNKNOWN_COMMAND = "Неизвестная команда. Отправьте текст задачи или /login."
UNSUPPORTED = "Пока принимаются только текстовые сообщения."


class TelegramMessenger:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(self, chat_id: int, text: str) -> None:
        await self._bot.send_message(chat_id=chat_id, text=text)


def applicant_name(user: User) -> str:
    """Name an admin can recognise; Identity accepts at most 64 characters."""
    name = f"{user.full_name} (@{user.username})" if user.username else user.full_name
    return name[:64]


def create_dispatcher(
    command: HandleBotCommand,
    accept: AcceptTextMessage | None = None,
    messenger: Messenger | None = None,
) -> Dispatcher:
    dispatcher = Dispatcher()

    @dispatcher.message(Command("start", "login", "join"))
    async def login(message: Message, event_update: Update) -> None:
        if message.from_user is None:
            return
        await command.execute(
            HandleBotCommandDto(
                update_id=event_update.update_id,
                telegram_id=message.from_user.id,
                chat_id=message.chat.id,
                text=message.text or "",
                private=message.chat.type == "private",
                display_name=applicant_name(message.from_user),
            )
        )

    if accept is None or messenger is None:
        return dispatcher

    @dispatcher.edited_message()
    async def edited(message: Message) -> None:
        """The text of a task is immutable (ADR-0005): edits change nothing."""

    @dispatcher.message(F.chat.type == "private", F.text, ~F.text.startswith("/"))
    async def text(
        message: Message, bot: Bot, received_at: datetime | None = None
    ) -> None:
        if message.from_user is None or message.text is None:
            return
        await accept.execute(
            AcceptTextDto(
                bot_id=bot.id,
                chat_id=message.chat.id,
                message_id=message.message_id,
                telegram_id=message.from_user.id,
                text=message.text,
                display_name=applicant_name(message.from_user),
                message_date=message.date,
                received_at=received_at or datetime.now(UTC),
            )
        )

    @dispatcher.message(F.chat.type == "private")
    async def other(message: Message) -> None:
        # Known commands are routed above; a job is never created for these.
        reply = UNKNOWN_COMMAND if (message.text or "").startswith("/") else UNSUPPORTED
        await messenger.send(message.chat.id, reply)

    return dispatcher
