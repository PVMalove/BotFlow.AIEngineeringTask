"""aiogram adapter; login codes are sent only through the Messenger port."""

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, Update, User

from application.commands.handle_bot_command import (
    HandleBotCommand,
    HandleBotCommandDto,
)


class TelegramMessenger:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(self, chat_id: int, text: str) -> None:
        await self._bot.send_message(chat_id=chat_id, text=text)


def applicant_name(user: User) -> str:
    """Name an admin can recognise; Identity accepts at most 64 characters."""
    name = f"{user.full_name} (@{user.username})" if user.username else user.full_name
    return name[:64]


def create_dispatcher(command: HandleBotCommand) -> Dispatcher:
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

    return dispatcher
