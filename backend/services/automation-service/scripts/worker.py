"""Automation worker: task creation and notifications.

Jobs -> CreateTask outbox -> RabbitMQ; TaskCreated -> job result and intent
(PostgreSQL); RabbitMQ consumer -> intents -> Redis Stream -> Telegram.
`replay <job_id>` re-sends the CreateTask of a failed job. Other worker commands
(transcription, notifications) are not implemented yet.
"""

import asyncio
import inspect
import logging
import os
import socket
import sys
from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx
from aiogram import Bot

from api.containers import Container, build_notifications, build_task_flow
from infrastructure.db.results import replay_job
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
            print("Worker step failed; retrying.", flush=True)
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
            tasks = build_task_flow(resources)
            await pipeline.queue.ensure_group()
            print("Automation worker started.", flush=True)
            async with asyncio.TaskGroup() as group:
                group.create_task(_forever(pipeline.consumer.run))
                group.create_task(repeat(pipeline.relay.execute))
                group.create_task(repeat(pipeline.notifier.execute))
                group.create_task(_forever(tasks.results.run))
                group.create_task(repeat(tasks.relay.execute))
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
            print("Worker consumer failed; retrying.", flush=True)
            await asyncio.sleep(FAILURE_SECONDS)


async def replay(job_id: UUID) -> None:
    container = Container()
    try:
        database = await container.database.async_()
        replayed = await replay_job(database.sessions, job_id)
    finally:
        shutdown = container.shutdown_resources()
        if inspect.isawaitable(shutdown):
            await shutdown
    if not replayed:
        raise SystemExit("Nothing to replay: no unpublished CreateTask for this job.")
    print("CreateTask of the job is due again.")


def _replay_job_id() -> UUID:
    try:
        return UUID(sys.argv[2])
    except ValueError:
        raise SystemExit("Usage: worker.py replay <job_id (UUID)>") from None


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "replay":
        asyncio.run(replay(_replay_job_id()))
    elif len(sys.argv) > 1:
        raise SystemExit("Automation worker handlers are not implemented.")
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            pass
        except Exception:
            raise SystemExit(
                "Automation worker unavailable; verify its secrets and services."
            ) from None
