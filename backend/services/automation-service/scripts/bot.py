"""Long-polling Telegram process. Reads credentials from Docker secret files."""

import asyncio
import inspect
import logging
import os

import httpx
from aiogram import Bot

from api.containers import Container
from infrastructure.identity_gateway import HttpIdentityGateway
from infrastructure.polling import TelegramPoller
from infrastructure.telegram import TelegramMessenger, create_dispatcher


async def main() -> None:
    container = Container()
    settings = container.settings()
    # aiogram/HTTP error tracebacks may contain Telegram URLs with the bot token.
    # This process emits only the fixed lifecycle messages below.
    logging.disable(logging.CRITICAL)
    try:
        bot = Bot(settings.secret("telegram_bot_token").get_secret_value())
        async with (
            bot,
            httpx.AsyncClient(
                base_url=os.environ.get(
                    "BOTFLOW_IDENTITY_URL", "http://identity-api:8000"
                ),
                timeout=10,
                follow_redirects=False,
            ) as client,
        ):
            identity = HttpIdentityGateway(
                client, settings.secret("automation_service_key").get_secret_value()
            )
            messenger = TelegramMessenger(bot)
            command = await container.bot_command.async_(
                identity=identity, messenger=messenger
            )
            accept = await container.accept_text.async_(
                identity=identity, messenger=messenger
            )
            accept_voice = await container.accept_voice.async_(
                identity=identity, messenger=messenger
            )
            dispatcher = create_dispatcher(command, accept, messenger, accept_voice)
            print("Telegram polling started.", flush=True)
            # Our own loop: the offset moves only after the handler returned.
            await TelegramPoller(bot, dispatcher).run()
    finally:
        shutdown = container.shutdown_resources()
        if inspect.isawaitable(shutdown):
            await shutdown


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        raise SystemExit(
            "Telegram bot unavailable; verify its secrets and services."
        ) from None
