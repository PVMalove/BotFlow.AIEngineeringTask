"""Automation worker: task creation, voice hand-off and notifications.

Jobs -> CreateTask outbox -> RabbitMQ; TaskCreated -> job result and intent
(PostgreSQL); RabbitMQ consumer -> intents -> Redis Stream -> Telegram; voice jobs
-> TranscribeVoice outbox -> Redis Stream (read by the STT worker).
`replay <job_id>` re-sends the CreateTask of a failed job or runs a failed voice
job again. `transcription` is the separate STT worker: it reads one voice record at
a time from the Redis Stream and needs the Whisper weights, not RabbitMQ.
"""

import asyncio
import inspect
import logging
import os
import signal
import socket
import sys
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from uuid import UUID

import httpx
from aiogram import Bot
from kernel_platform.resources import open_redis, open_s3

from api.containers import (
    Container,
    VoiceResources,
    build_notifications,
    build_task_flow,
    build_voice_flow,
)
from application.commands.transcribe_voice import TranscribeVoiceCommandHandler
from infrastructure.db.replay import replay_job, replay_voice_job
from infrastructure.db.unit_of_work import SqlAutomationUnitOfWork
from infrastructure.identity_gateway import HttpIdentityGateway
from infrastructure.s3_audio import S3AudioStore
from infrastructure.stt.runner import ReadinessMarker, SttConfig, run_worker
from infrastructure.stt.whisper_transcriber import WhisperTranscriber
from infrastructure.stt.whisper_validation import load_whisper_model, verify_weights
from infrastructure.telegram import TelegramMessenger
from infrastructure.telegram_files import TelegramFileSource

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
            voice = build_voice_flow(resources, consumer_name=socket.gethostname())
            await pipeline.queue.ensure_group()
            print("Automation worker started.", flush=True)
            async with asyncio.TaskGroup() as group:
                group.create_task(_forever(pipeline.consumer.run))
                group.create_task(repeat(pipeline.relay.execute))
                group.create_task(repeat(pipeline.notifier.execute))
                group.create_task(_forever(tasks.results.run))
                group.create_task(repeat(tasks.relay.execute))
                group.create_task(repeat(voice.relay.execute))
    finally:
        shutdown = container.shutdown_resources()
        if inspect.isawaitable(shutdown):
            await shutdown


async def transcription() -> None:
    """The only process that loads Whisper; ingress, AMQP and text never wait on it."""
    container = Container()
    settings = container.settings()
    config = SttConfig.from_environ(os.environ)
    # aiogram/HTTP error tracebacks may contain Telegram URLs with the bot token.
    logging.disable(logging.CRITICAL)
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    try:
        bot = Bot(settings.secret("telegram_bot_token").get_secret_value())
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(bot)
            database = await container.database.async_()
            redis = await stack.enter_async_context(open_redis(settings))
            s3 = await stack.enter_async_context(open_s3(settings))
            voice = build_voice_flow(
                VoiceResources(sessions=database.sessions, redis=redis),
                consumer_name=socket.gethostname(),
            )
            queue = voice.queue

            async def load() -> TranscribeVoiceCommandHandler:
                path = await asyncio.to_thread(
                    verify_weights, config.models_dir, config.model
                )
                model = await asyncio.to_thread(load_whisper_model, path)
                return TranscribeVoiceCommandHandler(
                    lambda: SqlAutomationUnitOfWork(database.sessions),
                    TelegramFileSource(bot),
                    S3AudioStore(s3, settings.s3_bucket),
                    WhisperTranscriber(model, timeout_seconds=config.timeout_seconds),
                )

            await run_worker(
                marker=ReadinessMarker(config.ready_file),
                load=load,
                queue=queue,
                stop=stop,
                # A timed-out decode thread cannot be stopped: replace the process.
                exit_process=lambda: os._exit(70),
            )
    finally:
        shutdown = container.shutdown_resources()
        if inspect.isawaitable(shutdown):
            await shutdown


async def replay(job_id: UUID) -> None:
    container = Container()
    try:
        database = await container.database.async_()
        # A failed voice job runs again from its stored audio; any other job
        # has its unpublished CreateTask made due again.
        replayed = await replay_voice_job(
            database.sessions, job_id
        ) or await replay_job(database.sessions, job_id)
    finally:
        shutdown = container.shutdown_resources()
        if inspect.isawaitable(shutdown):
            await shutdown
    if not replayed:
        raise SystemExit(
            "Nothing to replay: no failed voice job or unpublished CreateTask."
        )
    print("The job is due again.")


def _replay_job_id() -> UUID:
    try:
        return UUID(sys.argv[2])
    except ValueError:
        raise SystemExit("Usage: worker.py replay <job_id (UUID)>") from None


async def _forever(run: Callable[[], Awaitable[None]]) -> None:
    """Restart the consumer after a broker failure."""
    while True:
        try:
            await run()
        except Exception:
            print("Worker consumer failed; retrying.", flush=True)
            await asyncio.sleep(FAILURE_SECONDS)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "replay":
        asyncio.run(replay(_replay_job_id()))
    elif sys.argv[1:] == ["transcription"]:
        try:
            asyncio.run(transcription())
        except KeyboardInterrupt:
            pass
        except Exception:
            raise SystemExit(
                "STT worker unavailable; verify the Whisper weights, secrets and "
                "services."
            ) from None
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
