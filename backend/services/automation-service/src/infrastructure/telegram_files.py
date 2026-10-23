"""Telegram voice files: looked up by file_id, downloaded into memory only.

The download URL contains the bot token. aiogram builds it inside `download_file`;
this adapter never stores it and never lets an exception message escape.
"""

import io

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramNotFound

from application.commands.accept_voice import MAX_FILE_BYTES
from application.ports import AudioTooLarge, TelegramFileUnavailable, TransientIoError


class TelegramFileSource:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def fetch(self, file_id: str) -> bytes:
        try:
            file = await self._bot.get_file(file_id)
        except (TelegramBadRequest, TelegramNotFound):
            raise TelegramFileUnavailable() from None
        except Exception:
            raise TransientIoError() from None
        if file.file_path is None:
            raise TelegramFileUnavailable()
        if file.file_size is not None and file.file_size > MAX_FILE_BYTES:
            raise AudioTooLarge()
        buffer = _CappedBuffer()
        try:
            await self._bot.download_file(file.file_path, destination=buffer)
        except AudioTooLarge:
            raise
        except Exception as error:
            if getattr(error, "status", None) == 404:
                raise TelegramFileUnavailable() from None
            raise TransientIoError() from None
        return buffer.getvalue()


class _CappedBuffer(io.BytesIO):
    """Refuses to grow past the cap, whatever size Telegram declared."""

    def write(self, buffer: bytes, /) -> int:  # type: ignore[override]
        if self.tell() + len(buffer) > MAX_FILE_BYTES:
            raise AudioTooLarge()
        return super().write(buffer)
