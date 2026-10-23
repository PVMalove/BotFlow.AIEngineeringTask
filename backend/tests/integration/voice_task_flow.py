"""Voice message to task to notification, on real services.

Real Identity HTTP/PostgreSQL, Automation and Tasks PostgreSQL, RabbitMQ, Redis
Streams, MinIO and the real tasks-worker container; only the Telegram Bot API (the
Messenger and the file source) and speech recognition are replaced, on their ports.
The Automation steps (voice relay, STT worker loop, CreateTask relay, TaskCreated
consumer, notification relay and notifier) run in this process. Every row, stream
and object this class creates is removed again: the other smoke checks expect only
the initial admin and empty Automation/Tasks stores.
"""

import asyncio
import json
import re
import tempfile
import unittest
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock
from uuid import UUID, uuid4

import httpx
from api.containers import (
    build_bot_command,
    build_notifications,
    build_task_flow,
    build_voice_flow,
)
from application.commands.accept_text import (
    AcceptTextCommand,
    AcceptTextCommandHandler,
)
from application.commands.accept_voice import (
    AcceptVoiceCommand,
    AcceptVoiceCommandHandler,
)
from application.commands.handle_bot_command import HandleBotCommandDto
from application.commands.job_acceptance import Accepted, JobAcceptance
from application.commands.record_task_result import RecordTaskResultCommandHandler
from application.commands.replay_job import (
    ReplayJobCommand,
    ReplayJobCommandHandler,
    ReplayOutcome,
)
from application.commands.transcribe_voice import TranscribeVoiceCommandHandler
from application.ports import (
    AudioTooLong,
    CorruptedAudio,
    EmptyAudio,
    TelegramFileUnavailable,
    Transcript,
    TranscriptionTimeout,
)
from domain.entities.job import Job
from domain.entities.voice_message import VoiceMessage
from domain.notices import VOICE_FAILURES
from domain.value_objects.job_id import JobId
from infrastructure.db.unit_of_work import SqlAutomationUnitOfWork
from infrastructure.identity_gateway import HttpIdentityGateway
from infrastructure.messaging.envelopes import (
    VOICE_ROUTING_KEY,
    transcribe_outbox_id_for,
)
from infrastructure.messaging.task_result_consumer import TaskResultConsumer
from infrastructure.s3_audio import S3AudioStore
from infrastructure.streams import (
    NOTIFICATION_STREAM,
    VOICE_GROUP,
    VOICE_STREAM,
    RedisVoiceQueue,
)
from infrastructure.stt.runner import ReadinessMarker, run_worker
from kernel_domain.result import Result
from kernel_platform.messaging import declare_task_topology
from kernel_platform.resources import open_database, open_resources
from kernel_platform.retry import RetryPolicy
from kernel_platform.settings import Settings
from kernel_platform.streams import RedisStream
from sqlalchemy import text
from text_task_flow import TelegramPort, job_status

IDENTITY_URL = "http://identity-api:8000"
INGRESS_URL = "http://frontend:8080"
ORIGIN = {"Origin": INGRESS_URL}
BOT_ID = 7_800_001
AUTHOR_A = 6_300_000_001
AUTHOR_B = 6_300_000_002
LIMIT_AUTHORS = 6_400_000_001
RU_AUDIO = b"OggS-ru-voice-" + b"\x01" * 600
HY_AUDIO = b"OggS-hy-voice-" + b"\x02" * 600
RU_TEXT = "Купить молоко до пятницы"
HY_TEXT = "Գնել կաթ ուրբաթից առաջ"


class FakeFiles:
    """The Telegram file source at its port; counts every call."""

    def __init__(self) -> None:
        self.audio: dict[str, bytes] = {}
        self.calls: list[str] = []
        self.error: Exception | None = None

    async def fetch(self, file_id: str) -> bytes:
        self.calls.append(file_id)
        if self.error:
            raise self.error
        return self.audio[file_id]


class FakeStt:
    """Speech recognition at its port; can block, fail or answer per recording."""

    def __init__(self) -> None:
        # A list answers call by call; its last item repeats.
        self.script: dict[
            bytes, Transcript | Exception | list[Transcript | Exception]
        ] = {}
        self.heard: list[bytes] = []
        self.gate: asyncio.Event | None = None
        self.started = asyncio.Event()
        self.during: Callable[[], Awaitable[None]] | None = None

    async def transcribe(self, audio: bytes) -> Transcript:
        self.heard.append(audio)
        self.started.set()
        if self.during:
            await self.during()
        if self.gate:
            await self.gate.wait()
        outcome = self.script[audio]
        if isinstance(outcome, list):
            outcome = outcome.pop(0) if len(outcome) > 1 else outcome[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class VoiceTaskFlow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.stack = AsyncExitStack()
        self.addAsyncCleanup(self.stack.aclose)
        self.identity_db = await self.stack.enter_async_context(
            open_database(Settings(service="identity"))
        )
        self.automation_db = await self.stack.enter_async_context(
            open_database(Settings(service="automation"))
        )
        self.tasks_db = await self.stack.enter_async_context(
            open_database(Settings(service="tasks"))
        )
        self.internal = await self.stack.enter_async_context(
            httpx.AsyncClient(base_url=IDENTITY_URL, timeout=10)
        )
        self.settings = Settings(service="automation")
        key = self.settings.secret("automation_service_key")
        self.gateway = HttpIdentityGateway(self.internal, key.get_secret_value())
        self.resources = await self.stack.enter_async_context(
            open_resources(self.settings)
        )
        assert self.resources.redis is not None
        self.bucket = self.settings.s3_bucket
        self.telegram = TelegramPort()  # replies of the bot
        self.notifications = TelegramPort()  # messages sent by the notifier
        self.files = FakeFiles()
        self.stt = FakeStt()
        self.acceptance = JobAcceptance(self.uow)
        self.accept_voice = AcceptVoiceCommandHandler(
            self.gateway, self.uow, self.telegram
        )
        self.accept_text = AcceptTextCommandHandler(
            self.gateway, self.uow, self.telegram
        )
        self.login_bot = build_bot_command(
            self.automation_db, self.gateway, self.telegram
        )
        self.update_id = 3_000_000
        self.background: list[asyncio.Task[None]] = []
        self.worker_exits: list[int] = []
        self.worker_log: list[str] = []
        self.addAsyncCleanup(self.stop_background)
        await self.wipe()
        self.addAsyncCleanup(self.wipe)

    # ---- state --------------------------------------------------------------

    async def wipe(self) -> None:
        """Remove everything this class created; safe to run twice."""
        await self.stop_background()
        await self.settle_tasks_outbox()
        async with self.identity_db.sessions() as session:
            ids = "(SELECT id FROM members WHERE telegram_id BETWEEN :lo AND :hi)"
            params = {"lo": AUTHOR_A, "hi": LIMIT_AUTHORS + 1_000}
            await session.execute(text("DELETE FROM rate_limits"))
            await session.execute(
                text(f"DELETE FROM auth_sessions WHERE member_id IN {ids}"), params
            )
            await session.execute(
                text(f"DELETE FROM login_codes WHERE member_id IN {ids}"), params
            )
            await session.execute(
                text(f"DELETE FROM members WHERE id IN {ids}"), params
            )
            await session.commit()
        async with self.automation_db.sessions() as session:
            for table in (
                "jobs",  # voice_jobs follow by cascade
                "outbox",
                "notification_intents",
                "inbox_messages",
                "telegram_updates",
            ):
                await session.execute(text(f"DELETE FROM {table}"))
            await session.commit()
        async with self.tasks_db.sessions() as session:
            for table in ("tasks", "inbox_messages", "outbox"):
                await session.execute(text(f"DELETE FROM {table}"))
            await session.commit()
        channel = await self.resources.rabbitmq.channel()
        _, commands, results = await declare_task_topology(channel)
        await commands.purge()
        await results.purge()
        await self.resources.redis.delete(NOTIFICATION_STREAM, VOICE_STREAM)  # type: ignore[union-attr]
        listing = await self.resources.s3.list_objects_v2(
            Bucket=self.bucket, Prefix="voice/"
        )
        for item in listing.get("Contents", []):
            await self.resources.s3.delete_object(Bucket=self.bucket, Key=item["Key"])

    async def settle_tasks_outbox(self) -> None:
        """Wait until tasks-worker has nothing due, so no publish races a wipe."""
        for _ in range(100):
            async with self.tasks_db.sessions() as session:
                due = await session.scalar(
                    text(
                        "SELECT count(*) FROM outbox WHERE published_at IS NULL "
                        "AND next_attempt_at IS NOT NULL"
                    )
                )
            if not due:
                return
            await asyncio.sleep(0.3)
        self.fail("tasks-worker did not publish its outbox")

    async def stop_background(self) -> None:
        for task in self.background:
            task.cancel()
        await asyncio.gather(*self.background, return_exceptions=True)
        self.background.clear()

    async def rows(self, db: object, statement: str, **params: object) -> list[tuple]:
        async with db.sessions() as session:  # type: ignore[attr-defined]
            result = await session.execute(text(statement), params)
            return [tuple(row) for row in result]

    async def put_member(self, telegram_id: int, status: str = "approved") -> UUID:
        member_id = uuid4()
        async with self.identity_db.sessions() as session:
            await session.execute(
                text(
                    "INSERT INTO members (id, telegram_id, role, status, created_at) "
                    "VALUES (:id, :tg, 'user', :status, now())"
                ),
                {"id": member_id, "tg": telegram_id, "status": status},
            )
            await session.commit()
        return member_id

    async def login(self, telegram_id: int) -> httpx.AsyncClient:
        """A browser-like client of the ingress holding a fresh session cookie."""
        self.update_id += 1
        await self.login_bot.execute(
            HandleBotCommandDto(
                update_id=self.update_id,
                telegram_id=telegram_id,
                chat_id=telegram_id,
                text="/login",
                private=True,
            )
        )
        matched = re.search(
            r"Код входа: ([A-Za-z0-9_-]{32})\n", self.telegram.to(telegram_id)[-1]
        )
        self.assertIsNotNone(matched, "the bot must deliver a login code")
        assert matched is not None
        client = httpx.AsyncClient(base_url=INGRESS_URL, timeout=10)
        self.addAsyncCleanup(client.aclose)
        exchanged = await client.post(
            "/api/v1/auth/exchange", json={"code": matched.group(1)}, headers=ORIGIN
        )
        self.assertEqual(exchanged.status_code, 200)
        return client

    # ---- the voice path ------------------------------------------------------

    async def send_voice(
        self,
        telegram_id: int,
        message_id: int,
        audio: bytes = RU_AUDIO,
        *,
        duration: int = 12,
    ) -> AcceptVoiceCommand:
        file_id = f"file-{telegram_id}-{message_id}"
        self.files.audio[file_id] = audio
        now = datetime.now(UTC)
        command = AcceptVoiceCommand(
            bot_id=BOT_ID,
            chat_id=telegram_id,
            message_id=message_id,
            telegram_id=telegram_id,
            file_id=file_id,
            file_unique_id=f"unique-{file_id}",
            duration_seconds=duration,
            file_size_bytes=len(audio),
            mime_type="audio/ogg",
            display_name=f"Smoke {telegram_id}",
            message_date=now,
            received_at=now,
        )
        await self.accept_voice.execute(command)
        return command

    async def send_text(self, telegram_id: int, message_id: int, body: str) -> None:
        now = datetime.now(UTC)
        await self.accept_text.execute(
            AcceptTextCommand(
                bot_id=BOT_ID,
                chat_id=telegram_id,
                message_id=message_id,
                telegram_id=telegram_id,
                text=body,
                display_name=f"Smoke {telegram_id}",
                message_date=now,
                received_at=now,
            )
        )

    async def job(self, telegram_id: int, message_id: int) -> dict[str, object]:
        rows = await self.rows(
            self.automation_db,
            "SELECT j.id, j.status, j.text, j.task_id, j.last_error, j.acked_at, "
            "j.received_at, v.stage, v.s3_key, v.audio_bytes, v.audio_sha256, "
            "v.language, v.error_code, v.attempts, v.cold_start, v.started_at, "
            "v.stored_at, v.stt_started_at, v.stt_finished_at, v.file_id "
            "FROM jobs j JOIN voice_jobs v ON v.job_id = j.id "
            "WHERE j.bot_id = :b AND j.chat_id = :c AND j.message_id = :m",
            b=BOT_ID,
            c=telegram_id,
            m=message_id,
        )
        self.assertEqual(len(rows), 1)
        names = (
            "id status text task_id last_error acked_at received_at stage s3_key "
            "audio_bytes audio_sha256 language error_code attempts cold_start "
            "started_at stored_at stt_started_at stt_finished_at file_id"
        ).split()
        return dict(zip(names, rows[0], strict=True))

    async def start_flow(self, *, stuck_after_ms: int = 1_000):  # type: ignore[no-untyped-def]
        """Relays, the TaskCreated consumer and the notifier, in this process."""
        voice = build_voice_flow(self.resources, consumer_name=f"relay-{uuid4().hex}")
        tasks = build_task_flow(self.resources)
        pipeline = build_notifications(
            self.resources,
            self.gateway,
            self.notifications,
            consumer_name=f"smoke-{uuid4().hex}",
            stuck_after_ms=stuck_after_ms,
        )
        await pipeline.queue.ensure_group()
        self.background.append(asyncio.create_task(tasks.results.run()))
        return voice, tasks, pipeline

    def start_worker(self, *, stuck_after_ms: int = 60_000) -> asyncio.Task[None]:
        """The real STT loop and use case; only Telegram files and STT are fakes."""
        queue = RedisVoiceQueue(
            RedisStream(
                self.resources.redis,  # type: ignore[arg-type]
                VOICE_STREAM,
                VOICE_GROUP,
                f"stt-{uuid4().hex}",
            ),
            stuck_after_ms,
        )
        handler = TranscribeVoiceCommandHandler(
            self.uow,
            self.files,
            S3AudioStore(self.resources.s3, self.bucket),
            self.stt,
            policy=RetryPolicy(jitter=0.0),
        )

        async def load() -> TranscribeVoiceCommandHandler:
            return handler

        async def quick(seconds: float) -> None:
            await asyncio.sleep(min(seconds, 0.05))

        directory = tempfile.mkdtemp(prefix="botflow-stt-")
        patch = mock.patch("infrastructure.stt.runner.BLOCK_MS", 200)
        patch.start()
        self.addCleanup(patch.stop)
        task = asyncio.create_task(
            run_worker(
                marker=ReadinessMarker(Path(directory) / "ready"),
                load=load,
                queue=queue,
                stop=asyncio.Event(),
                exit_process=lambda: self.worker_exits.append(1),
                sleep=quick,
                log=self.worker_log.append,
            )
        )
        self.background.append(task)
        return task

    async def pump(self, voice, tasks, pipeline, done) -> None:  # type: ignore[no-untyped-def]
        """Drive the relays and the notifier until `done()` holds."""
        for _ in range(300):
            await voice.relay.execute()
            await tasks.relay.execute()
            await pipeline.relay.execute()
            await pipeline.notifier.execute(block_ms=100)
            if await done():
                return
            await asyncio.sleep(0.1)
        self.fail("the voice path did not settle in time")

    async def tasks_with_text(self, body: str) -> list[tuple]:
        return await self.rows(
            self.tasks_db,
            "SELECT id, source_key, status, version, author_name FROM tasks "
            "WHERE text = :t ORDER BY created_at",
            t=body,
        )

    async def object_bytes(self, key: str) -> bytes:
        response = await self.resources.s3.get_object(Bucket=self.bucket, Key=key)
        async with response["Body"] as body:
            return bytes(await body.read())

    async def idle_in_transaction(self) -> int:
        rows = await self.rows(
            self.automation_db,
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
            "AND state LIKE 'idle in transaction%' AND pid <> pg_backend_pid()",
        )
        return int(rows[0][0])

    # ---- RU and hy recordings become tasks --------------------------------------

    async def test_ru_voice_becomes_a_task_and_its_author_is_notified(self) -> None:
        member_id = await self.put_member(AUTHOR_A)
        self.stt.script[RU_AUDIO] = Transcript(RU_TEXT, "ru")
        committed: list[bool] = []

        async def visible_to_another_connection() -> None:
            # The ACK is being sent: job, voice record and outbox row are committed.
            counts = await self.rows(
                self.automation_db,
                "SELECT (SELECT count(*) FROM jobs), "
                "(SELECT count(*) FROM voice_jobs), (SELECT count(*) FROM outbox)",
            )
            committed.append(counts == [(1, 1, 1)])

        self.telegram.before_send = visible_to_another_connection
        sent = await self.send_voice(AUTHOR_A, 1)

        self.assertEqual(committed, [True])
        job = await self.job(AUTHOR_A, 1)
        job_id = JobId.from_source(BOT_ID, AUTHOR_A, 1).value
        self.assertEqual(job["id"], job_id)
        self.assertEqual(
            (job["status"], job["stage"], job["text"]), ("accepted", "queued", None)
        )
        self.assertEqual(job["file_id"], sent.file_id)
        self.assertIsNotNone(job["acked_at"])
        queued = await self.rows(
            self.automation_db,
            "SELECT id, type, routing_key, published_at, correlation_id FROM outbox",
        )
        self.assertEqual(
            queued,
            [
                (
                    transcribe_outbox_id_for(job_id),
                    "TranscribeVoice",
                    VOICE_ROUTING_KEY,
                    None,
                    job_id,
                )
            ],
        )

        seen_at_stt: list[tuple] = []

        async def checkpoint_is_stored() -> None:
            # While recognition runs, the audio is already checkpointed in MinIO.
            seen_at_stt.append(
                (
                    (await self.job(AUTHOR_A, 1))["stage"],
                    await self.object_bytes(f"voice/{job_id}"),
                )
            )

        self.stt.during = checkpoint_is_stored
        voice, tasks, pipeline = await self.start_flow()
        self.start_worker()

        async def notified() -> bool:
            return bool(self.notifications.to(AUTHOR_A))

        await self.pump(voice, tasks, pipeline, notified)

        self.assertEqual(seen_at_stt, [("stored", RU_AUDIO)])
        self.assertEqual(
            self.notifications.to(AUTHOR_A), [f"Задача создана: {RU_TEXT}"]
        )
        job = await self.job(AUTHOR_A, 1)
        self.assertEqual((job["status"], job["stage"]), ("completed", "done"))
        self.assertEqual((job["text"], job["language"]), (RU_TEXT, "ru"))
        self.assertEqual(job["s3_key"], f"voice/{job_id}")
        self.assertEqual(job["audio_bytes"], len(RU_AUDIO))
        self.assertEqual(len(str(job["audio_sha256"])), 64)
        self.assertEqual((job["attempts"], job["last_error"]), (1, None))
        for mark in ("started_at", "stored_at", "stt_started_at", "stt_finished_at"):
            self.assertIsNotNone(job[mark], mark)
        self.assertEqual(self.files.calls, [sent.file_id])
        (
            (_, source_key, task_status, version, author_name),
        ) = await self.tasks_with_text(RU_TEXT)
        self.assertEqual(
            (source_key, task_status, version, author_name),
            (f"{BOT_ID}:{AUTHOR_A}:1", "pending", 1, f"Smoke {AUTHOR_A}"),
        )
        outbox_rows = await self.rows(
            self.automation_db,
            "SELECT routing_key, published_at IS NOT NULL FROM outbox "
            "ORDER BY routing_key",
        )
        self.assertEqual(
            outbox_rows, [("task.create", True), (VOICE_ROUTING_KEY, True)]
        )
        intents = await self.rows(
            self.automation_db,
            "SELECT kind, status, member_id FROM notification_intents",
        )
        self.assertEqual(intents, [("task.result", "sent", member_id)])

        # The REST card shows the transcript as the text of the task.
        browser = await self.login(AUTHOR_A)
        card = await browser.get(f"/api/v1/tasks/{job['task_id']}")
        self.assertEqual(card.status_code, 200)
        self.assertEqual(card.json()["data"]["source_type"], "voice")
        data = card.json()["data"]
        self.assertEqual((data["text"], data["author_id"]), (RU_TEXT, str(member_id)))

        # The object is private, and no Telegram URL or token was stored anywhere.
        async with httpx.AsyncClient() as anonymous:
            response = await anonymous.get(
                f"http://minio:9000/{self.bucket}/voice/{job_id}"
            )
        self.assertEqual(response.status_code, 403)
        for table in ("jobs", "voice_jobs", "outbox", "notification_intents"):
            leaked = await self.rows(
                self.automation_db,
                f"SELECT count(*) FROM {table} t "
                "WHERE t::text LIKE '%api.telegram.org%' OR t::text LIKE '%/file/bot%'",
            )
            self.assertEqual(leaked, [(0,)], table)

    async def test_hy_voice_becomes_one_task_with_the_armenian_text(self) -> None:
        await self.put_member(AUTHOR_A)
        self.stt.script[HY_AUDIO] = Transcript(HY_TEXT, "hy")
        await self.send_voice(AUTHOR_A, 2, HY_AUDIO)
        voice, tasks, pipeline = await self.start_flow()
        self.start_worker()

        async def notified() -> bool:
            return bool(self.notifications.to(AUTHOR_A))

        await self.pump(voice, tasks, pipeline, notified)

        job = await self.job(AUTHOR_A, 2)
        self.assertEqual((job["status"], job["language"]), ("completed", "hy"))
        self.assertEqual(len(await self.tasks_with_text(HY_TEXT)), 1)
        self.assertEqual(
            self.notifications.to(AUTHOR_A), [f"Задача создана: {HY_TEXT}"]
        )

    # ---- repeats ---------------------------------------------------------------

    async def test_a_repeated_update_and_a_redelivered_entry_make_one_task(
        self,
    ) -> None:
        await self.put_member(AUTHOR_A)
        self.stt.script[RU_AUDIO] = Transcript(RU_TEXT, "ru")
        await self.send_voice(AUTHOR_A, 1)
        await self.send_voice(AUTHOR_A, 1)  # Telegram redelivered the update
        self.assertEqual(len(self.telegram.to(AUTHOR_A)), 1)  # one ACK
        self.assertEqual(
            await self.rows(
                self.automation_db,
                "SELECT (SELECT count(*) FROM jobs), "
                "(SELECT count(*) FROM voice_jobs), (SELECT count(*) FROM outbox)",
            ),
            [(1, 1, 1)],
        )
        voice, tasks, pipeline = await self.start_flow()
        self.start_worker()

        async def notified() -> bool:
            return bool(self.notifications.to(AUTHOR_A))

        await self.pump(voice, tasks, pipeline, notified)
        job_id = JobId.from_source(BOT_ID, AUTHOR_A, 1).value

        # The same entry arrives again after the job is done.
        await self.resources.redis.xadd(  # type: ignore[union-attr]
            VOICE_STREAM, {"job_id": str(job_id), "outbox_id": str(uuid4())}
        )
        for _ in range(100):
            (group,) = await self.resources.redis.xinfo_groups(VOICE_STREAM)  # type: ignore[union-attr]
            if group["pending"] == 0 and group["lag"] == 0:
                break
            await asyncio.sleep(0.1)
        self.assertEqual((group["pending"], group["lag"]), (0, 0))

        self.assertEqual(len(self.stt.heard), 1)
        self.assertEqual(len(self.files.calls), 1)
        ((_, source_key, *_),) = await self.tasks_with_text(RU_TEXT)
        self.assertEqual(source_key, f"{BOT_ID}:{AUTHOR_A}:1")
        self.assertEqual((await self.job(AUTHOR_A, 1))["id"], job_id)

    # ---- final errors -----------------------------------------------------------

    async def failed_voice(
        self, error: Exception, code: str, *, files_error: Exception | None = None
    ) -> None:
        await self.put_member(AUTHOR_A)
        self.stt.script[RU_AUDIO] = error
        self.files.error = files_error
        await self.send_voice(AUTHOR_A, 1)
        voice, tasks, pipeline = await self.start_flow()
        self.start_worker()

        async def notified() -> bool:
            return bool(self.notifications.to(AUTHOR_A))

        await self.pump(voice, tasks, pipeline, notified)

        job = await self.job(AUTHOR_A, 1)
        self.assertEqual((job["status"], job["stage"]), ("failed", "failed"))
        self.assertEqual((job["error_code"], job["last_error"]), (code, code))
        self.assertIsNone(job["task_id"])
        self.assertEqual(self.notifications.to(AUTHOR_A), [VOICE_FAILURES[code]])
        self.assertEqual(
            await self.rows(self.tasks_db, "SELECT count(*) FROM tasks"), [(0,)]
        )
        self.assertEqual(
            await self.rows(
                self.automation_db,
                "SELECT routing_key FROM outbox",
            ),
            [(VOICE_ROUTING_KEY,)],
        )  # no CreateTask, so no empty card
        self.assertEqual(
            await self.rows(
                self.automation_db,
                "SELECT count(*) FROM jobs WHERE status = 'accepted'",
            ),
            [(0,)],
        )  # the slot is free
        intents = await self.rows(
            self.automation_db, "SELECT kind, status FROM notification_intents"
        )
        self.assertEqual(intents, [("transcription.result", "sent")])

    async def test_a_recording_without_speech_fails_with_a_notice_and_no_task(
        self,
    ) -> None:
        await self.failed_voice(EmptyAudio(), "empty_audio")

    async def test_a_corrupted_recording_fails_with_a_notice_and_no_task(self) -> None:
        await self.failed_voice(CorruptedAudio(), "corrupted_audio")

    async def test_a_recording_that_is_too_long_fails_with_a_notice(self) -> None:
        await self.failed_voice(AudioTooLong(), "too_long")

    async def test_a_telegram_file_that_is_gone_fails_before_any_storage(self) -> None:
        await self.failed_voice(
            EmptyAudio(), "file_unavailable", files_error=TelegramFileUnavailable()
        )
        self.assertEqual(self.stt.heard, [])
        listing = await self.resources.s3.list_objects_v2(
            Bucket=self.bucket, Prefix="voice/"
        )
        self.assertEqual(listing.get("KeyCount", 0), 0)

    async def test_a_timeout_fails_the_job_and_asks_the_worker_to_restart(
        self,
    ) -> None:
        await self.failed_voice(TranscriptionTimeout(), "timeout")
        for _ in range(50):
            if self.worker_exits:
                break
            await asyncio.sleep(0.1)
        self.assertEqual(self.worker_exits, [1])
        await asyncio.wait_for(self.background[-1], timeout=5)  # the loop has ended

    # ---- limits -----------------------------------------------------------------

    def new_voice_job(self, author_id: UUID, telegram_id: int, message_id: int) -> Job:
        now = datetime.now(UTC)
        return Job.accept_voice(
            VoiceMessage(
                file_id=f"limit-{telegram_id}-{message_id}",
                file_unique_id=f"limit-unique-{telegram_id}-{message_id}",
                duration_seconds=5,
                file_size_bytes=100,
                mime_type="audio/ogg",
            ),
            bot_id=BOT_ID,
            chat_id=telegram_id,
            message_id=message_id,
            author_id=author_id,
            author_telegram_id=telegram_id,
            author_name="Smoke",
            message_date=now,
            received_at=now,
            now=now,
        )

    def new_text_job(self, author_id: UUID, telegram_id: int, message_id: int) -> Job:
        now = datetime.now(UTC)
        return Job.accept_text(
            bot_id=BOT_ID,
            chat_id=telegram_id,
            message_id=message_id,
            author_id=author_id,
            author_telegram_id=telegram_id,
            author_name="Smoke",
            text="текст",
            message_date=now,
            received_at=now,
            now=now,
        )

    def uow(self) -> SqlAutomationUnitOfWork:
        return SqlAutomationUnitOfWork(self.automation_db.sessions)

    @staticmethod
    def outcome_of(result: Result[Accepted]) -> str:
        """accepted / duplicate, or the name of the limit that refused the job."""
        if result.is_ok:
            return result.value.outcome.value
        return {"job_limit": "limit", "voice_limit": "voice_limit"}[result.error.code]

    async def test_voice_limits_ten_and_two_hold_under_concurrent_acceptance(
        self,
    ) -> None:
        authors = [(uuid4(), LIMIT_AUTHORS + n) for n in range(8)]
        batch = [
            self.new_voice_job(author_id, telegram_id, number)
            for author_id, telegram_id in authors
            for number in range(4)
        ]

        outcomes = await asyncio.gather(*(self.acceptance.accept(j) for j in batch))

        pairs = list(zip(batch, outcomes, strict=True))
        stored = [job for job, o in pairs if self.outcome_of(o) == "accepted"]
        self.assertEqual(len(stored), 10)  # 10 voice jobs in total
        self.assertEqual(
            {self.outcome_of(o) for _, o in pairs}, {"accepted", "voice_limit"}
        )
        per_author = await self.rows(
            self.automation_db,
            "SELECT author_id, count(*) FROM jobs WHERE kind = 'voice' "
            "GROUP BY author_id",
        )
        self.assertTrue(all(count <= 2 for _, count in per_author))  # 2 each
        self.assertEqual(sum(count for _, count in per_author), 10)
        self.assertEqual(
            await self.rows(self.automation_db, "SELECT count(*) FROM voice_jobs"),
            [(10,)],
        )
        # A repeated message is no new load, even at the limit.
        first_job = stored[0]
        again = await self.acceptance.accept(first_job)
        self.assertEqual(self.outcome_of(again), "duplicate")

        # The voice slot is freed by the result (TaskCreated), not before it.
        author_id = first_job.author_id
        telegram_id = first_job.author_telegram_id
        blocked = await self.acceptance.accept(
            self.new_voice_job(author_id, telegram_id, 90)
        )
        self.assertEqual(self.outcome_of(blocked), "voice_limit")
        await self.rows_exec(
            "UPDATE jobs SET text = 'расшифровка' WHERE id = :id",
            id=first_job.id.value,
        )
        result = {
            "envelope_version": 1,
            "payload": {
                "job_id": str(first_job.id.value),
                "task_id": str(uuid4()),
            },
        }
        await TaskResultConsumer(RecordTaskResultCommandHandler(self.uow)).handle(
            str(uuid4()), "TaskCreated", json.dumps(result).encode()
        )
        freed = await self.acceptance.accept(
            self.new_voice_job(author_id, telegram_id, 91)
        )
        self.assertEqual(self.outcome_of(freed), "accepted")

        # ... or by a final error.
        other_job = stored[-1]
        async with self.uow() as uow:
            running = await uow.jobs.get(other_job.id, lock=True)
            assert running is not None
            now = datetime.now(UTC)
            running.begin_voice_attempt(now, False)
            self.assertTrue(
                running.fail_voice("empty_audio", VOICE_FAILURES["empty_audio"], now)
            )
            await uow.jobs.update(running)
            await uow.commit()
        freed_again = await self.acceptance.accept(
            self.new_voice_job(other_job.author_id, other_job.author_telegram_id, 92)
        )
        self.assertEqual(self.outcome_of(freed_again), "accepted")

    async def test_the_common_limits_still_apply_to_voice_jobs(self) -> None:
        # 9 text jobs plus one voice job fill this author's 10 common slots.
        author_id, telegram_id = uuid4(), LIMIT_AUTHORS
        for number in range(9):
            outcome = await self.acceptance.accept(
                self.new_text_job(author_id, telegram_id, number)
            )
            self.assertEqual(self.outcome_of(outcome), "accepted")
        one = await self.acceptance.accept(
            self.new_voice_job(author_id, telegram_id, 50)
        )
        self.assertEqual(self.outcome_of(one), "accepted")
        second = await self.acceptance.accept(
            self.new_voice_job(author_id, telegram_id, 51)
        )
        self.assertEqual(self.outcome_of(second), "limit")  # common 10 per author
        self.assertEqual(
            await self.rows(self.automation_db, "SELECT count(*) FROM voice_jobs"),
            [(1,)],
        )

        # 100 unfinished jobs in total close the door to voice as well.
        for number, other in enumerate(range(LIMIT_AUTHORS + 1, LIMIT_AUTHORS + 10)):
            for message_id in range(10):
                await self.acceptance.accept(
                    self.new_text_job(uuid4(), other, number * 100 + message_id)
                )
        total = await self.rows(
            self.automation_db, "SELECT count(*) FROM jobs WHERE status = 'accepted'"
        )
        self.assertEqual(total, [(100,)])
        shut = await self.acceptance.accept(
            self.new_voice_job(uuid4(), LIMIT_AUTHORS + 500, 1)
        )
        self.assertEqual(self.outcome_of(shut), "limit")

    async def rows_exec(self, statement: str, **params: object) -> None:
        async with self.automation_db.sessions() as session:
            await session.execute(text(statement), params)
            await session.commit()

    # ---- a crash after the checkpoint -------------------------------------------

    async def test_a_record_taken_over_after_a_crash_resumes_from_minio(self) -> None:
        await self.put_member(AUTHOR_A)
        # The first recognition crashes after the checkpoint; the next one succeeds.
        self.stt.script[RU_AUDIO] = [
            RuntimeError("model crashed"),
            Transcript(RU_TEXT, "ru"),
        ]
        sent = await self.send_voice(AUTHOR_A, 1)
        voice, tasks, pipeline = await self.start_flow()
        self.start_worker(stuck_after_ms=300)

        async def notified() -> bool:
            return bool(self.notifications.to(AUTHOR_A))

        await self.pump(voice, tasks, pipeline, notified)

        self.assertEqual(self.files.calls, [sent.file_id])  # Telegram asked only once
        self.assertEqual(len(self.stt.heard), 2)  # the record was taken over
        self.assertEqual(len(await self.tasks_with_text(RU_TEXT)), 1)
        job = await self.job(AUTHOR_A, 1)
        self.assertEqual((job["status"], job["stage"]), ("completed", "done"))
        self.assertEqual(job["attempts"], 2)
        self.assertEqual(
            job["s3_key"], f"voice/{JobId.from_source(BOT_ID, AUTHOR_A, 1).value}"
        )

    # ---- error, replay and restart ------------------------------------------------

    async def test_a_failed_voice_job_replays_from_the_stored_object_into_one_task(
        self,
    ) -> None:
        await self.put_member(AUTHOR_A)
        # The first recognition ends with a final error; the replayed one succeeds.
        self.stt.script[RU_AUDIO] = [CorruptedAudio(), Transcript(RU_TEXT, "ru")]
        sent = await self.send_voice(AUTHOR_A, 1)
        job_id = JobId.from_source(BOT_ID, AUTHOR_A, 1).value
        voice, tasks, pipeline = await self.start_flow()
        self.start_worker()

        async def failure_told() -> bool:
            return bool(self.notifications.to(AUTHOR_A))

        await self.pump(voice, tasks, pipeline, failure_told)

        shown = await job_status(self.automation_db, job_id)
        self.assertEqual(
            (shown["kind"], shown["status"], shown["stage"]),
            ("voice", "failed", "failed"),
        )
        self.assertEqual(
            (shown["processing_attempts"], shown["error_code"], shown["max_attempts"]),
            ("1", "corrupted_audio", "3"),
        )
        self.assertNotIn(RU_TEXT, "".join(shown.values()))
        self.assertIsNotNone((await self.job(AUTHOR_A, 1))["s3_key"])  # checkpoint kept

        handler = ReplayJobCommandHandler(self.uow)
        replayed = await handler.execute(ReplayJobCommand(job_id))
        self.assertEqual(replayed.value, ReplayOutcome.REPLAYED)
        job = await self.job(AUTHOR_A, 1)
        self.assertEqual(
            (job["id"], job["status"], job["stage"]), (job_id, "accepted", "stored")
        )
        self.assertEqual(job["attempts"], 0)

        async def task_told() -> bool:
            return len(self.notifications.to(AUTHOR_A)) == 2

        await self.pump(voice, tasks, pipeline, task_told)

        self.assertEqual(self.files.calls, [sent.file_id])  # the stored object was used
        self.assertEqual(len(self.stt.heard), 2)
        self.assertEqual(len(await self.tasks_with_text(RU_TEXT)), 1)
        job = await self.job(AUTHOR_A, 1)
        self.assertEqual((job["status"], job["stage"]), ("completed", "done"))
        self.assertEqual(
            (await handler.execute(ReplayJobCommand(job_id))).value,
            ReplayOutcome.UNCHANGED,
        )

        # A restarted worker meets the old stream entry again: no second task.
        await self.resources.redis.xadd(  # type: ignore[union-attr]
            VOICE_STREAM, {"job_id": str(job_id), "outbox_id": str(uuid4())}
        )
        for _ in range(100):
            (group,) = await self.resources.redis.xinfo_groups(VOICE_STREAM)  # type: ignore[union-attr]
            if group["pending"] == 0 and group["lag"] == 0:
                break
            await asyncio.sleep(0.1)
        self.assertEqual((group["pending"], group["lag"]), (0, 0))
        self.assertEqual(len(self.stt.heard), 2)
        self.assertEqual(len(await self.tasks_with_text(RU_TEXT)), 1)

    # ---- recognition holds no transaction and blocks nothing ---------------------

    async def test_a_blocked_recognition_holds_no_transaction_and_blocks_no_path(
        self,
    ) -> None:
        await self.put_member(AUTHOR_A)
        await self.put_member(AUTHOR_B)
        self.stt.script[RU_AUDIO] = Transcript(RU_TEXT, "ru")
        self.stt.gate = asyncio.Event()
        idle_counts: list[int] = []

        async def count_idle() -> None:
            idle_counts.append(await self.idle_in_transaction())

        self.stt.during = count_idle
        await self.send_voice(AUTHOR_A, 1)
        voice, tasks, pipeline = await self.start_flow()
        self.start_worker()

        async def recognising() -> bool:
            return self.stt.started.is_set()

        await self.pump(voice, tasks, pipeline, recognising)
        self.assertEqual(idle_counts, [0])  # no 'idle in transaction' meanwhile

        # Text acceptance, the text path and the notifier all finish while STT blocks.
        await self.send_text(AUTHOR_B, 1, "Текст во время распознавания")

        async def text_notified() -> bool:
            return bool(self.notifications.to(AUTHOR_B))

        await self.pump(voice, tasks, pipeline, text_notified)
        self.assertEqual(
            self.notifications.to(AUTHOR_B),
            ["Задача создана: Текст во время распознавания"],
        )
        self.assertEqual(await self.idle_in_transaction(), 0)
        self.assertEqual((await self.job(AUTHOR_A, 1))["status"], "accepted")

        self.stt.gate.set()

        async def voice_notified() -> bool:
            return bool(self.notifications.to(AUTHOR_A))

        await self.pump(voice, tasks, pipeline, voice_notified)
        self.assertEqual(len(await self.tasks_with_text(RU_TEXT)), 1)

    # ---- the bucket ---------------------------------------------------------------

    async def test_the_audio_bucket_is_created_by_the_bootstrap_and_is_private(
        self,
    ) -> None:
        await self.resources.s3.head_bucket(Bucket=self.bucket)
        async with httpx.AsyncClient() as anonymous:
            listing = await anonymous.get(f"http://minio:9000/{self.bucket}/")
        self.assertEqual(listing.status_code, 403)
