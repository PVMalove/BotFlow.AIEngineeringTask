"""Text message to task to notification, on real services.

Real Identity HTTP/PostgreSQL, Automation and Tasks PostgreSQL, RabbitMQ, Redis,
the ingress and the real tasks-worker container; only the Telegram Bot API is
replaced, at the Messenger port. The Automation steps (CreateTask relay, TaskCreated
consumer, notification relay and notifier) run in this process. A broker outage and
a slow Telegram cannot be produced from inside the smoke container, so they are
substituted at the publisher and messenger seams over the real PostgreSQL rows.
Every row this class creates is removed again: the other smoke checks expect only
the initial admin and empty Automation/Tasks stores.
"""

import asyncio
import json
import re
import unittest
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
from api.containers import build_bot_command, build_notifications, build_task_flow
from application.commands.accept_text import (
    ACK,
    AcceptTextCommand,
    AcceptTextCommandHandler,
)
from application.commands.handle_bot_command import HandleBotCommandDto
from application.commands.job_acceptance import AcceptOutcome, JobAcceptance
from application.commands.record_task_result import RecordTaskResultCommandHandler
from application.commands.replay_job import (
    ReplayJobCommand,
    ReplayJobCommandHandler,
    ReplayOutcome,
)
from application.queries.job_status import render, view_of
from domain.entities.job import Job
from domain.job_limits import JobLimits
from domain.value_objects.job_id import JobId
from infrastructure.db.job_status_reader import read_job_status
from infrastructure.db.relay_hooks import fail_job
from infrastructure.db.tables import outbox
from infrastructure.db.unit_of_work import SqlAutomationUnitOfWork
from infrastructure.identity_gateway import HttpIdentityGateway
from infrastructure.messaging.envelopes import outbox_id_for
from infrastructure.messaging.task_result_consumer import TaskResultConsumer
from infrastructure.streams import NOTIFICATION_STREAM
from kernel_platform.messaging import (
    TASK_CREATE_ROUTING_KEY,
    ConfirmedPublisher,
    declare_task_topology,
)
from kernel_platform.outbox import RelayOutbox, sql_transactions
from kernel_platform.resources import open_database, open_resources
from kernel_platform.retry import RetryPolicy
from kernel_platform.settings import Settings
from sqlalchemy import text

IDENTITY_URL = "http://identity-api:8000"
INGRESS_URL = "http://frontend:8080"
ORIGIN = {"Origin": INGRESS_URL}
BOT_ID = 7_700_001
AUTHOR_A = 6_100_000_001
AUTHOR_B = 6_100_000_002
AUTHOR_C = 6_100_000_003
AUTHORS = (6_100_000_001, 6_100_000_099)
LIMIT_AUTHORS = 6_200_000_001
UNBOUND_ROUTING_KEY = "task.unbound"  # no queue is bound: the broker returns it
POLICY = RetryPolicy(jitter=0.0)


async def job_status(db: object, job_id: UUID) -> dict[str, str]:
    """The operator status of a job as `key=value` fields (what the CLI prints)."""
    async with db.sessions() as session:  # type: ignore[attr-defined]
        record = await read_job_status(session, job_id)
    assert record is not None
    return dict(line.split("=", 1) for line in render(view_of(record)).splitlines())


class TelegramPort:
    """The Bot API at the Messenger port; `before_send` runs inside `send`."""

    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []
        self.failure: Exception | None = None
        self.before_send: Callable[[], Awaitable[None]] | None = None

    async def send(self, chat_id: int, message: str) -> None:
        if self.before_send:
            await self.before_send()
        if self.failure:
            raise self.failure
        self.messages.append((chat_id, message))

    def to(self, chat_id: int) -> list[str]:
        return [text for chat, text in self.messages if chat == chat_id]


class BrokerDown:
    """The publisher port of a relay whose broker cannot be reached."""

    async def publish(
        self, routing_key: str, body: bytes, *, message_id: str, type: str
    ) -> None:
        raise ConnectionRefusedError("broker down")


class TextTaskFlow(unittest.IsolatedAsyncioTestCase):
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
        key = Settings(service="automation").secret("automation_service_key")
        self.gateway = HttpIdentityGateway(self.internal, key.get_secret_value())
        self.telegram = TelegramPort()  # replies of the bot
        self.notifications = TelegramPort()  # messages sent by the notifier
        self.accept = AcceptTextCommandHandler(self.gateway, self.uow, self.telegram)
        self.login_bot = build_bot_command(
            self.automation_db, self.gateway, self.telegram
        )
        self.update_id = 2_000_000
        self.background: list[asyncio.Task[None]] = []
        self.addAsyncCleanup(self.stop_background)
        await self.wipe()
        self.addAsyncCleanup(self.wipe)

    def uow(self) -> SqlAutomationUnitOfWork:
        return SqlAutomationUnitOfWork(self.automation_db.sessions)

    # ---- state --------------------------------------------------------------

    async def wipe(self) -> None:
        """Remove everything this class created; safe to run twice."""
        await self.stop_background()
        await self.settle_tasks_outbox()
        async with self.identity_db.sessions() as session:
            ids = "(SELECT id FROM members WHERE telegram_id BETWEEN :lo AND :hi)"
            params = {"lo": AUTHORS[0], "hi": LIMIT_AUTHORS + 1_000}
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
                "jobs",
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
        async with open_resources(Settings(service="automation")) as resources:
            channel = await resources.rabbitmq.channel()
            _, commands, results = await declare_task_topology(channel)
            await commands.purge()
            await results.purge()
            assert resources.redis is not None
            await resources.redis.delete(NOTIFICATION_STREAM)

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

    async def send_text(
        self, telegram_id: int, message_id: int, body: str = "Купить молоко"
    ) -> AcceptTextCommand:
        now = datetime.now(UTC)
        command = AcceptTextCommand(
            bot_id=BOT_ID,
            chat_id=telegram_id,
            message_id=message_id,
            telegram_id=telegram_id,
            text=body,
            display_name=f"Smoke {telegram_id}",
            message_date=now,
            received_at=now,
        )
        await self.accept.execute(command)
        return command

    async def job(self, telegram_id: int, message_id: int) -> tuple:
        rows = await self.rows(
            self.automation_db,
            "SELECT id, status, task_id, last_error, acked_at, received_at "
            "FROM jobs WHERE bot_id = :b AND chat_id = :c AND message_id = :m",
            b=BOT_ID,
            c=telegram_id,
            m=message_id,
        )
        self.assertEqual(len(rows), 1)
        return rows[0]

    async def start_flow(self, *, stuck_after_ms: int = 1_000):  # type: ignore[no-untyped-def]
        """The Automation steps of the text path, running in this process."""
        resources = await self.stack.enter_async_context(
            open_resources(Settings(service="automation"))
        )
        flow = build_task_flow(resources)
        pipeline = build_notifications(
            resources,
            self.gateway,
            self.notifications,
            consumer_name=f"smoke-{uuid4().hex}",
            stuck_after_ms=stuck_after_ms,
        )
        await pipeline.queue.ensure_group()
        self.background.append(asyncio.create_task(flow.results.run()))
        return resources, flow, pipeline

    async def pump(self, flow, pipeline, done) -> None:  # type: ignore[no-untyped-def]
        """Drive the relays and the notifier until `done()` holds."""
        for _ in range(200):
            await flow.relay.execute()
            await pipeline.relay.execute()
            await pipeline.notifier.execute(block_ms=100)
            if await done():
                return
            await asyncio.sleep(0.2)
        self.fail("the text path did not settle in time")

    async def tasks_with_text(self, body: str) -> list[tuple]:
        return await self.rows(
            self.tasks_db,
            "SELECT id, source_key, status, version, author_name FROM tasks "
            "WHERE text = :t ORDER BY created_at",
            t=body,
        )

    # ---- the text path ------------------------------------------------------

    async def test_text_becomes_a_task_and_its_author_is_notified(self) -> None:
        member_id = await self.put_member(AUTHOR_A)
        committed: list[bool] = []

        async def visible_to_another_connection() -> None:
            # The ACK is being sent: the job and its outbox row must already be
            # committed, that is visible to a different connection.
            jobs = await self.rows(self.automation_db, "SELECT count(*) FROM jobs")
            queued = await self.rows(self.automation_db, "SELECT count(*) FROM outbox")
            committed.append(jobs == [(1,)] and queued == [(1,)])

        self.telegram.before_send = visible_to_another_connection
        sent = await self.send_text(AUTHOR_A, 1)

        self.assertEqual(committed, [True])
        self.assertEqual(self.telegram.to(AUTHOR_A), [ACK])
        job_id, status, task_id, _, acked_at, received_at = await self.job(AUTHOR_A, 1)
        self.assertEqual((status, task_id), ("accepted", None))
        self.assertEqual(job_id, JobId.from_source(BOT_ID, AUTHOR_A, 1).value)
        self.assertLess(acked_at - received_at, timedelta(seconds=2))
        self.assertGreaterEqual(acked_at, received_at)
        self.assertEqual(received_at, sent.received_at)
        queued = await self.rows(
            self.automation_db,
            "SELECT id, routing_key, published_at, correlation_id FROM outbox",
        )
        self.assertEqual(
            queued,
            [(outbox_id_for(job_id), TASK_CREATE_ROUTING_KEY, None, job_id)],
        )

        _, flow, pipeline = await self.start_flow()

        async def notified() -> bool:
            return bool(self.notifications.to(AUTHOR_A))

        await self.pump(flow, pipeline, notified)

        self.assertEqual(
            self.notifications.to(AUTHOR_A), ["Задача создана: Купить молоко"]
        )
        (
            (_, source_key, task_status, version, author_name),
        ) = await self.tasks_with_text("Купить молоко")
        self.assertEqual(
            (source_key, task_status, version, author_name),
            (f"{BOT_ID}:{AUTHOR_A}:1", "pending", 1, f"Smoke {AUTHOR_A}"),
        )
        _, status, task_id, last_error, _, _ = await self.job(AUTHOR_A, 1)
        self.assertEqual((status, last_error), ("completed", None))
        self.assertIsNotNone(task_id)
        intents = await self.rows(
            self.automation_db,
            "SELECT kind, status, member_id FROM notification_intents",
        )
        self.assertEqual(intents, [("task.result", "sent", member_id)])

        # The REST card and list through the ingress show the same task.
        browser = await self.login(AUTHOR_A)
        card = await browser.get(f"/api/v1/tasks/{task_id}")
        self.assertEqual(card.status_code, 200)
        self.assertEqual(card.json()["data"]["source_type"], "text")
        data = card.json()["data"]
        self.assertEqual(
            (data["text"], data["status"], data["version"], data["author_id"]),
            ("Купить молоко", "pending", 1, str(member_id)),
        )
        self.assertEqual(data["author_name"], f"Smoke {AUTHOR_A}")
        listed = (await browser.get("/api/v1/tasks")).json()
        self.assertEqual([t["id"] for t in listed["data"]], [str(task_id)])

    async def test_a_repeated_message_makes_no_second_job_but_the_same_text_does(
        self,
    ) -> None:
        await self.put_member(AUTHOR_A)

        await self.send_text(AUTHOR_A, 1)
        await self.send_text(AUTHOR_A, 1)  # Telegram redelivered the update
        self.assertEqual(self.telegram.to(AUTHOR_A), [ACK])
        self.assertEqual(
            await self.rows(self.automation_db, "SELECT count(*) FROM jobs"), [(1,)]
        )
        await self.send_text(AUTHOR_A, 2)  # a new message with the same text

        _, flow, pipeline = await self.start_flow()

        async def two_tasks() -> bool:
            return len(await self.tasks_with_text("Купить молоко")) == 2

        await self.pump(flow, pipeline, two_tasks)
        self.assertEqual(
            sorted(row[1] for row in await self.tasks_with_text("Купить молоко")),
            [f"{BOT_ID}:{AUTHOR_A}:1", f"{BOT_ID}:{AUTHOR_A}:2"],
        )

    async def test_a_lost_reply_is_repeated_only_while_no_ack_was_recorded(
        self,
    ) -> None:
        await self.put_member(AUTHOR_A)
        self.telegram.failure = ConnectionError("telegram down")

        await self.send_text(AUTHOR_A, 1)  # stored, but the reply did not arrive

        self.assertEqual((await self.job(AUTHOR_A, 1))[4], None)
        self.telegram.failure = None
        await self.send_text(AUTHOR_A, 1)
        await self.send_text(AUTHOR_A, 1)
        self.assertEqual(self.telegram.to(AUTHOR_A), [ACK])
        self.assertIsNotNone((await self.job(AUTHOR_A, 1))[4])
        self.assertEqual(
            await self.rows(self.automation_db, "SELECT count(*) FROM jobs"), [(1,)]
        )

    async def test_people_without_access_get_no_job(self) -> None:
        await self.put_member(AUTHOR_A, status="blocked")

        await self.send_text(AUTHOR_A, 1)
        await self.send_text(AUTHOR_B, 1)  # not a member at all

        self.assertEqual(
            await self.rows(self.automation_db, "SELECT count(*) FROM jobs"), [(0,)]
        )
        self.assertIn("Доступ не предоставлен", self.telegram.to(AUTHOR_A)[0])
        self.assertIn("Доступ не предоставлен", self.telegram.to(AUTHOR_B)[0])

    # ---- atomicity and limits -------------------------------------------------

    def new_job(self, author_id: UUID, telegram_id: int, message_id: int) -> Job:
        now = datetime.now(UTC)
        return Job.accept_text(
            bot_id=BOT_ID,
            chat_id=telegram_id,
            message_id=message_id,
            author_id=author_id,
            author_telegram_id=telegram_id,
            author_name="Smoke",
            text="лимит",
            message_date=now,
            received_at=now,
            now=now,
        )

    async def test_the_limits_hold_under_concurrent_acceptance(self) -> None:
        acceptance = JobAcceptance(self.uow)
        authors = [(uuid4(), LIMIT_AUTHORS + n) for n in range(12)]
        batch = [
            self.new_job(author_id, telegram_id, number)
            for author_id, telegram_id in authors
            for number in range(12)
        ]

        outcomes = await asyncio.gather(*(acceptance.accept(job) for job in batch))

        stored = [
            job
            for job, outcome in zip(batch, outcomes, strict=True)
            if outcome.is_ok and outcome.value.outcome is AcceptOutcome.ACCEPTED
        ]
        self.assertEqual(len(stored), 100)  # 100 in total
        per_author = await self.rows(
            self.automation_db,
            "SELECT author_id, count(*) FROM jobs GROUP BY author_id",
        )
        self.assertTrue(all(count <= 10 for _, count in per_author))  # 10 each
        self.assertEqual(sum(count for _, count in per_author), 100)
        self.assertEqual(
            await self.rows(self.automation_db, "SELECT count(*) FROM outbox"),
            [(100,)],
        )
        # A repeated message is not new load, even at the limit.
        finished = stored[0]
        again = await acceptance.accept(finished)
        self.assertTrue(again.is_ok)
        self.assertIs(again.value.outcome, AcceptOutcome.DUPLICATE)

        # The slot is freed by the result, not by the notification.
        result = {
            "envelope_version": 1,
            "payload": {"job_id": str(finished.id.value), "task_id": str(uuid4())},
        }
        await TaskResultConsumer(RecordTaskResultCommandHandler(self.uow)).handle(
            str(uuid4()), "TaskCreated", json.dumps(result).encode()
        )
        freed = await acceptance.accept(
            self.new_job(finished.author_id, LIMIT_AUTHORS, 99)
        )
        self.assertTrue(freed.is_ok)
        self.assertIs(freed.value.outcome, AcceptOutcome.ACCEPTED)
        full = await acceptance.accept(
            self.new_job(finished.author_id, LIMIT_AUTHORS, 98)
        )
        self.assertTrue(full.is_err)
        self.assertEqual(full.error.code, "job_limit")

    async def test_a_failed_transaction_leaves_no_job_and_sends_no_ack(self) -> None:
        member_id = await self.put_member(AUTHOR_A)
        job = self.new_job(member_id, AUTHOR_A, 1)

        # The outbox insert fails after the job's own insert, in the same transaction.
        with (
            patch(
                "infrastructure.db.outbox_events.enqueue",
                side_effect=RuntimeError("outbox down"),
            ),
            self.assertRaises(RuntimeError),
        ):
            await JobAcceptance(self.uow).accept(job)

        for table in ("jobs", "outbox"):
            self.assertEqual(
                await self.rows(self.automation_db, f"SELECT count(*) FROM {table}"),
                [(0,)],
            )
        self.assertEqual(self.telegram.messages, [])

    async def test_an_unreachable_database_stores_nothing_and_sends_no_ack(
        self,
    ) -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        await self.put_member(AUTHOR_A)
        engine = create_async_engine(
            "postgresql+asyncpg://automation:none@127.0.0.1:1/automation"
        )
        self.addAsyncCleanup(engine.dispose)
        accept = AcceptTextCommandHandler(
            self.gateway,
            lambda: SqlAutomationUnitOfWork(async_sessionmaker(engine)),
            self.telegram,
        )
        now = datetime.now(UTC)

        with self.assertRaises(Exception):
            await accept.execute(
                AcceptTextCommand(
                    BOT_ID, AUTHOR_A, 1, AUTHOR_A, "текст", "Smoke", now, now
                )
            )

        self.assertEqual(self.telegram.messages, [])
        self.assertEqual(
            await self.rows(self.automation_db, "SELECT count(*) FROM jobs"), [(0,)]
        )

    # ---- retry, exhaustion and replay ------------------------------------------

    async def test_a_broker_outage_spends_no_attempt_and_the_path_resumes(
        self,
    ) -> None:
        await self.put_member(AUTHOR_A)
        await self.send_text(AUTHOR_A, 1, "Пока брокер недоступен")
        resources, flow, pipeline = await self.start_flow()
        start = datetime.now(UTC)
        relay = RelayOutbox(
            sql_transactions(resources.sessions, outbox),
            BrokerDown(),
            clock=lambda: start,
            policy=POLICY,
            on_exhausted=fail_job,
        )

        for _ in range(4):  # four outages in a row: more than the three attempts
            await relay.execute()
            start += timedelta(seconds=5)

        ((attempts, error, next_attempt),) = await self.rows(
            self.automation_db,
            "SELECT attempts, last_error, next_attempt_at FROM outbox",
        )
        self.assertEqual(attempts, 0)
        self.assertEqual(error, "publish failed: ConnectionRefusedError")
        self.assertIsNotNone(next_attempt)
        self.assertEqual((await self.job(AUTHOR_A, 1))[1], "accepted")

        async def completed() -> bool:
            return (await self.job(AUTHOR_A, 1))[1] == "completed"

        await self.pump(flow, pipeline, completed)  # the broker is back
        self.assertEqual(len(await self.tasks_with_text("Пока брокер недоступен")), 1)

    async def test_retries_follow_the_schedule_then_fail_the_job_and_replay_is_safe(
        self,
    ) -> None:
        await self.put_member(AUTHOR_B)
        await self.send_text(AUTHOR_B, 10, "Позвонить маме")
        job_id = JobId.from_source(BOT_ID, AUTHOR_B, 10).value
        entry = outbox_id_for(job_id)
        await self.rows_exec(
            "UPDATE outbox SET routing_key = :k WHERE id = :id",
            k=UNBOUND_ROUTING_KEY,
            id=entry,
        )
        resources, flow, pipeline = await self.start_flow()
        clock = datetime.now(UTC) + timedelta(seconds=1)
        relay = RelayOutbox(
            sql_transactions(resources.sessions, outbox),
            ConfirmedPublisher(resources.rabbitmq, declare_task_topology),
            clock=lambda: clock,
            policy=POLICY,
            on_exhausted=fail_job,
        )

        async def state() -> tuple:
            (row,) = await self.rows(
                self.automation_db,
                "SELECT attempts, next_attempt_at, last_error FROM outbox",
            )
            return row

        await relay.execute()
        attempts, next_attempt, error = await state()
        self.assertEqual((attempts, next_attempt), (1, clock + timedelta(seconds=5)))
        self.assertTrue(error.startswith("publish failed:"))
        await relay.execute()  # not due yet: nothing happens
        self.assertEqual((await state())[0], 1)
        clock += timedelta(seconds=5)
        await relay.execute()
        attempts, next_attempt, _ = await state()
        self.assertEqual((attempts, next_attempt), (2, clock + timedelta(seconds=30)))
        clock += timedelta(seconds=30)
        await relay.execute()
        attempts, next_attempt, error = await state()
        self.assertEqual((attempts, next_attempt), (3, None))
        _, status, _, job_error, _, _ = await self.job(AUTHOR_B, 10)
        self.assertEqual((status, job_error), ("failed", error))  # slot is free
        self.assertEqual(
            await self.rows(
                self.automation_db,
                "SELECT count(*) FROM jobs WHERE status = 'accepted'",
            ),
            [(0,)],
        )

        shown = await job_status(self.automation_db, job_id)
        self.assertEqual(
            (shown["status"], shown["delivery_attempts"], shown["max_attempts"]),
            ("failed", "3", "3"),
        )
        self.assertEqual(
            (shown["error_code"], shown["next_attempt_at"], shown["task_created"]),
            ("delivery_failed", "-", "no"),
        )
        self.assertNotIn("Позвонить маме", "".join(shown.values()))

        # The limit applies again: with the slot taken, the replay is refused.
        await self.send_text(AUTHOR_B, 11, "Вторая задача")
        refused = await ReplayJobCommandHandler(
            self.uow, JobLimits(per_author=1)
        ).execute(ReplayJobCommand(job_id))
        self.assertTrue(refused.is_err)
        self.assertEqual(refused.error.code, "job_limit")
        self.assertEqual((await self.job(AUTHOR_B, 10))[1], "failed")
        self.assertEqual(
            await self.rows(
                self.automation_db,
                "SELECT attempts, next_attempt_at FROM outbox WHERE id = :id",
                id=entry,
            ),
            [(3, None)],
        )

        # The operator fixed the cause and replays the original job.
        await self.rows_exec(
            "UPDATE outbox SET routing_key = :k WHERE id = :id",
            k=TASK_CREATE_ROUTING_KEY,
            id=entry,
        )
        self.assertEqual(await self.replay(job_id), ReplayOutcome.REPLAYED)
        self.assertEqual((await self.job(AUTHOR_B, 10))[1], "accepted")

        async def completed() -> bool:
            return (await self.job(AUTHOR_B, 10))[1] == "completed" and (
                await self.job(AUTHOR_B, 11)
            )[1] == "completed"

        await self.pump(flow, pipeline, completed)
        self.assertEqual(len(await self.tasks_with_text("Позвонить маме")), 1)
        self.assertEqual(len(await self.tasks_with_text("Вторая задача")), 1)
        self.assertEqual(
            (await job_status(self.automation_db, job_id))["task_created"], "yes"
        )
        self.assertEqual(await self.replay(job_id), ReplayOutcome.UNCHANGED)

        # Another copy of the same command (a new message id) makes no second task.
        resends = ConfirmedPublisher(resources.rabbitmq, declare_task_topology)
        (payload,) = await self.rows(
            self.automation_db,
            "SELECT payload::text FROM outbox WHERE id = :id",
            id=entry,
        )
        copy_id = uuid4()
        await resends.publish(
            TASK_CREATE_ROUTING_KEY,
            payload[0].encode(),
            message_id=str(copy_id),
            type="CreateTask",
        )

        async def copy_consumed() -> bool:
            return await self.rows(
                self.tasks_db,
                "SELECT 1 FROM inbox_messages WHERE message_id = :m",
                m=str(copy_id),
            ) == [(1,)]

        for _ in range(100):
            if await copy_consumed():
                break
            await asyncio.sleep(0.2)
        self.assertTrue(await copy_consumed())
        self.assertEqual(len(await self.tasks_with_text("Позвонить маме")), 1)

    async def test_a_replay_after_the_task_was_created_makes_no_second_task(
        self,
    ) -> None:
        await self.put_member(AUTHOR_C)
        await self.send_text(AUTHOR_C, 20, "Оплатить счёт")
        job_id = JobId.from_source(BOT_ID, AUTHOR_C, 20).value
        entry = outbox_id_for(job_id)
        # The broker took the command, but its confirm was lost: the retries ran out.
        lost = "publish failed: TimeoutError"
        await self.rows_exec(
            "UPDATE outbox SET attempts = 3, next_attempt_at = NULL, last_error = :e "
            "WHERE id = :id",
            e=lost,
            id=entry,
        )
        await self.rows_exec(
            "UPDATE jobs SET status = 'failed', last_error = :e WHERE id = :id",
            e=lost,
            id=job_id,
        )
        resources, flow, pipeline = await self.start_flow()
        (payload,) = await self.rows(
            self.automation_db,
            "SELECT payload::text FROM outbox WHERE id = :id",
            id=entry,
        )
        await ConfirmedPublisher(resources.rabbitmq, declare_task_topology).publish(
            TASK_CREATE_ROUTING_KEY,
            payload[0].encode(),
            message_id=str(entry),
            type="CreateTask",
        )

        async def completed() -> bool:
            return (await self.job(AUTHOR_C, 20))[1] == "completed"

        await self.pump(flow, pipeline, completed)  # the late result wins

        outbox_sql = "SELECT attempts, next_attempt_at, published_at FROM outbox"
        stored = await self.rows(self.automation_db, outbox_sql)
        for _ in range(2):  # the command repeated after the task was committed
            self.assertEqual(await self.replay(job_id), ReplayOutcome.UNCHANGED)
        await flow.relay.execute()
        self.assertEqual(await self.rows(self.automation_db, outbox_sql), stored)
        self.assertEqual(stored, [(3, None, None)])  # the exhausted row is untouched
        self.assertEqual(len(await self.tasks_with_text("Оплатить счёт")), 1)
        self.assertEqual((await self.job(AUTHOR_C, 20))[0], job_id)
        shown = await job_status(self.automation_db, job_id)
        self.assertEqual((shown["status"], shown["task_created"]), ("completed", "yes"))

    async def replay(self, job_id: UUID) -> ReplayOutcome:
        result = await ReplayJobCommandHandler(self.uow).execute(
            ReplayJobCommand(job_id)
        )
        return result.value

    async def rows_exec(self, statement: str, **params: object) -> None:
        async with self.automation_db.sessions() as session:
            await session.execute(text(statement), params)
            await session.commit()

    # ---- notification failure --------------------------------------------------

    async def test_a_failed_notification_does_not_cancel_the_task(self) -> None:
        await self.put_member(AUTHOR_A)
        self.notifications.failure = ConnectionError("telegram down")
        await self.send_text(AUTHOR_A, 1, "Задача без уведомления")
        _, flow, pipeline = await self.start_flow()

        async def intent_failed() -> bool:
            rows = await self.rows(
                self.automation_db, "SELECT status FROM notification_intents"
            )
            return rows == [("failed",)]

        await self.pump(flow, pipeline, intent_failed)

        self.assertEqual(self.notifications.messages, [])
        self.assertEqual((await self.job(AUTHOR_A, 1))[1], "completed")
        self.assertEqual(len(await self.tasks_with_text("Задача без уведомления")), 1)

    # ---- REST contract through the ingress ---------------------------------------

    async def test_rest_list_card_cursor_and_status_filter(self) -> None:
        member_id = await self.put_member(AUTHOR_C)
        browser = await self.login(AUTHOR_C)
        base = datetime.now(UTC) - timedelta(hours=1)
        statuses = ["pending", "in_progress", "pending", "completed", "pending"]
        ids: list[UUID] = []
        async with self.tasks_db.sessions() as session:
            for number, status in enumerate(statuses * 2):
                task_id = uuid4()
                ids.append(task_id)
                created = base + timedelta(minutes=number // 2)  # pairs share a time
                await session.execute(
                    text(
                        "INSERT INTO tasks (id, source_key, correlation_id, text, "
                        "status, version, author_id, author_telegram_id, author_name, "
                        "created_at, updated_at) VALUES (:id, :key, :corr, :text, "
                        ":status, 1, :author, :tg, :name, :at, :at)"
                    ),
                    {
                        "id": task_id,
                        "key": f"rest:{number}",
                        "corr": uuid4(),
                        "text": f"REST {number}",
                        "status": status,
                        "author": member_id if number % 2 else uuid4(),
                        "tg": AUTHOR_C,
                        "name": f"Автор {number % 2}",
                        "at": created,
                    },
                )
            await session.commit()

        seen: list[str] = []
        cursor: str | None = None
        while True:
            params = {"limit": "3", **({"cursor": cursor} if cursor else {})}
            page = await browser.get("/api/v1/tasks", params=params)
            self.assertEqual(page.status_code, 200)
            body = page.json()
            seen += [task["id"] for task in body["data"]]
            cursor = body["meta"]["next_cursor"]
            if not body["meta"]["has_more"]:
                self.assertIsNone(cursor)
                break
        self.assertEqual(len(seen), 10)
        self.assertEqual(len(set(seen)), 10)  # no gap or overlap, shared times too
        newest_first = (await browser.get("/api/v1/tasks")).json()["data"]
        self.assertEqual([t["id"] for t in newest_first], seen)
        self.assertEqual(len(newest_first), 10)  # the default page holds 50

        self.assertEqual(
            (await browser.get("/api/v1/tasks", params={"limit": "100"})).status_code,
            200,
        )
        self.assertEqual(
            (await browser.get("/api/v1/tasks", params={"limit": "101"})).status_code,
            422,
        )
        done = await browser.get("/api/v1/tasks", params={"status": "completed"})
        self.assertEqual(
            [t["status"] for t in done.json()["data"]], ["completed", "completed"]
        )
        broken = await browser.get("/api/v1/tasks", params={"cursor": "garbage"})
        self.assertEqual(broken.status_code, 400)
        card = await browser.get(f"/api/v1/tasks/{seen[0]}")
        self.assertEqual(card.status_code, 200)
        self.assertEqual(card.json()["data"]["id"], seen[0])
        self.assertEqual(
            (await browser.get(f"/api/v1/tasks/{uuid4()}")).status_code, 404
        )

        anonymous = httpx.AsyncClient(base_url=INGRESS_URL, timeout=10)
        self.addAsyncCleanup(anonymous.aclose)
        self.assertEqual((await anonymous.get("/api/v1/tasks")).status_code, 401)

        # Access is re-read on every request: a blocked member's session is refused.
        await self.identity_sql(
            "UPDATE members SET status = 'blocked' WHERE id = :id", id=member_id
        )
        self.assertEqual((await browser.get("/api/v1/tasks")).status_code, 401)

    async def identity_sql(self, statement: str, **params: object) -> None:
        async with self.identity_db.sessions() as session:
            await session.execute(text(statement), params)
            await session.commit()
