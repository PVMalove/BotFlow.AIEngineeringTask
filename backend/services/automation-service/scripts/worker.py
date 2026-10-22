"""Automation worker: delivers access-decision notifications.

RabbitMQ consumer -> notification intents (PostgreSQL) -> Redis Stream -> Telegram.
Other worker commands (transcription, notifications) are not implemented yet.
"""

import asyncio
import inspect
import logging
import os
import socket
import sys
from collections.abc import Awaitable, Callable

import httpx
from aiogram import Bot

from api.containers import Container, build_notifications
from infrastructure.identity_gateway import HttpIdentityGateway
from infrastructure.telegram import TelegramMessenger

IDLE_SECONDS = 1.0
FAILURE_SECONDS = 5.0


async def repeat(step: Callable[[], Awaitable[int]]) -> None:
    """Run `step` forever; pause when idle and after a failure."""
    while True:
        try:
            if not await step():
                await asyncio.sleep(IDLE_SECONDS)
        except Exception:
            # Driver errors can carry credentials; report nothing but a fixed text.
            print("Notification step failed; retrying.", flush=True)
            await asyncio.sleep(FAILURE_SECONDS)


async def main() -> None:
    container = Container()
    settings = container.settings()
    # aiogram/HTTP error tracebacks may contain Telegram URLs with the bot token.
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
            resources = await container.resources.async_()
            pipeline = build_notifications(
                resources,
                HttpIdentityGateway(
                    client, settings.secret("automation_service_key").get_secret_value()
                ),
                TelegramMessenger(bot),
                consumer_name=socket.gethostname(),
            )
            await pipeline.queue.ensure_group()
            print("Access notifications started.", flush=True)
            async with asyncio.TaskGroup() as group:
                group.create_task(_forever(pipeline.consumer.run))
                group.create_task(repeat(pipeline.relay.execute))
                group.create_task(repeat(pipeline.notifier.execute))
    finally:
        shutdown = container.shutdown_resources()
        if inspect.isawaitable(shutdown):
            await shutdown


async def _forever(run: Callable[[], Awaitable[None]]) -> None:
    """Restart the consumer after a broker failure."""
    while True:
        try:
            await run()
        except Exception:
            print("Notification consumer failed; retrying.", flush=True)
            await asyncio.sleep(FAILURE_SECONDS)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        raise SystemExit("Automation worker handlers are not implemented.")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        raise SystemExit(
            "Automation worker unavailable; verify its secrets and services."
        ) from None
