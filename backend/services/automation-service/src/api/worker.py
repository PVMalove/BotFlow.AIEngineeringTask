"""Automation worker: task creation, voice hand-off and notifications.

Jobs -> CreateTask outbox -> RabbitMQ; TaskCreated -> job result and intent
(PostgreSQL); RabbitMQ consumer -> intents -> Redis Stream -> Telegram; voice jobs
-> TranscribeVoice outbox -> Redis Stream (read by the STT worker).
`status <job_id>` prints the safe state of one job; `replay <job_id>` re-sends the
CreateTask of a failed job or runs a failed voice job again. `transcription` is the
separate STT worker: it reads one voice record at a time from the Redis Stream and
needs the Whisper weights, not RabbitMQ.
"""

import asyncio
import logging
import os
import signal
import socket
import sys
from contextlib import AsyncExitStack
from uuid import UUID

import httpx
from aiogram import Bot
from kernel_platform.resources import open_redis, open_s3
from kernel_platform.worker import forever, repeat, run_process, shutdown

from api.containers import (
    Container,
    VoiceResources,
    build_notifications,
    build_task_flow,
    build_voice_flow,
)
from application.commands.transcribe_voice import TranscribeVoiceCommandHandler
from application.queries.job_status import render, view_of
from infrastructure.db.job_status_reader import read_job_status
from infrastructure.db.replay import replay_job, replay_voice_job
from infrastructure.db.unit_of_work import SqlAutomationUnitOfWork
from infrastructure.identity_gateway import HttpIdentityGateway
from infrastructure.s3_audio import S3AudioStore
from infrastructure.stt.runner import ReadinessMarker, SttConfig, run_worker
from infrastructure.stt.whisper_transcriber import WhisperTranscriber
from infrastructure.stt.whisper_validation import load_whisper_model, verify_weights
from infrastructure.telegram import TelegramMessenger
from infrastructure.telegram_files import TelegramFileSource


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
                group.create_task(forever(pipeline.consumer.run))
                group.create_task(repeat(pipeline.relay.execute))
                group.create_task(repeat(pipeline.notifier.execute))
                group.create_task(forever(tasks.results.run))
                group.create_task(repeat(tasks.relay.execute))
                group.create_task(repeat(voice.relay.execute))
    finally:
        await shutdown(container)


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
        await shutdown(container)


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
        await shutdown(container)
    if not replayed:
        raise SystemExit(
            "Nothing to replay: no failed voice job or unpublished CreateTask."
        )
    print("The job is due again.")


async def status(job_id: UUID) -> None:
    container = Container()
    try:
        database = await container.database.async_()
        async with database.sessions() as session:
            record = await read_job_status(session, job_id)
    finally:
        await shutdown(container)
    if record is None:
        raise SystemExit("Job not found.")
    print(render(view_of(record)))


def _job_id_argument(command: str) -> UUID:
    try:
        return UUID(sys.argv[2])
    except ValueError:
        raise SystemExit(
            f"Usage: python -m api.worker {command} <job_id (UUID)>"
        ) from None


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "replay":
        asyncio.run(replay(_job_id_argument("replay")))
    elif len(sys.argv) == 3 and sys.argv[1] == "status":
        asyncio.run(status(_job_id_argument("status")))
    elif sys.argv[1:] == ["transcription"]:
        run_process(
            transcription,
            unavailable="STT worker unavailable; verify the Whisper weights, secrets "
            "and services.",
        )
    elif len(sys.argv) > 1:
        raise SystemExit("Automation worker handlers are not implemented.")
    else:
        run_process(
            main,
            unavailable="Automation worker unavailable; verify secrets and services.",
        )
