"""aiogram adapter; login codes are sent only through the Messenger port."""

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, Update

from application.commands.handle_bot_command import HandleBotCommand


class TelegramMessenger:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(self, chat_id: int, text: str) -> None:
        await self._bot.send_message(chat_id=chat_id, text=text, protect_content=True)


def create_dispatcher(command: HandleBotCommand) -> Dispatcher:
    dispatcher = Dispatcher()

    @dispatcher.message(Command("start", "login"))
    async def login(message: Message, event_update: Update) -> None:
        if message.from_user is None:
            return
        await command(
            update_id=event_update.update_id,
            telegram_id=message.from_user.id,
            chat_id=message.chat.id,
            text=message.text or "",
            private=message.chat.type == "private",
        )

    return dispatcher
