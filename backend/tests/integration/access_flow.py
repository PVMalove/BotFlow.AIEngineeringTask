"""Join requests, access decisions and decision notifications on real services.

Real Identity HTTP/PostgreSQL, RabbitMQ, Redis and the identity-worker; only the
Telegram Bot API is replaced, at the Messenger port. The admin API is called on
identity-api directly because the ingress has no `/api/v1/admin/` route yet.
Every row this class creates is removed again: the other smoke checks expect the
initial admin to be the only member.
"""

import asyncio
import json
import re
import unittest
from contextlib import AsyncExitStack
from uuid import UUID, uuid4

import httpx
from api.containers import build_bot_command, build_notifications
from application.commands.handle_bot_command import HandleBotCommandDto
from botflow_platform.messaging import (
    ACCESS_DECIDED_ROUTING_KEY,
    AUTOMATION_IDENTITY_QUEUE,
    ConfirmedPublisher,
    declare_identity_events,
)
from botflow_platform.resources import open_database, open_resources
from botflow_platform.settings import Settings
from infrastructure.identity_gateway import HttpIdentityGateway
from infrastructure.streams import NOTIFICATION_STREAM
from sqlalchemy import text

IDENTITY_URL = "http://identity-api:8000"
ORIGIN = {"Origin": IDENTITY_URL}
ADMIN_A = 4_200_100_001
ADMIN_B = 4_200_100_002
USER_FIRST = 5_000_000_001
USER_LAST = 5_000_000_099
ADMIN_RANGE = (4_200_100_001, 4_200_100_099)
APPROVED_TEXT = "Заявка одобрена. Для входа в веб-панель отправьте /login."
REJECTED_TEXT = "Заявка отклонена. Подать её снова можно командой /join."


class TelegramPort:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send(self, chat_id: int, message: str) -> None:
        self.messages.append((chat_id, message))

    def to(self, chat_id: int) -> list[str]:
        return [text for chat, text in self.messages if chat == chat_id]


class AccessFlow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.stack = AsyncExitStack()
        self.addAsyncCleanup(self.stack.aclose)
        self.identity_db = await self.stack.enter_async_context(
            open_database(Settings(service="identity"))
        )
        self.automation_db = await self.stack.enter_async_context(
            open_database(Settings(service="automation"))
        )
        self.internal = await self.stack.enter_async_context(
            httpx.AsyncClient(base_url=IDENTITY_URL, timeout=10)
        )
        self.browser = await self.stack.enter_async_context(
            httpx.AsyncClient(base_url="http://frontend:8080", timeout=10)
        )
        key = Settings(service="automation").secret("automation_service_key")
        self.gateway = HttpIdentityGateway(self.internal, key.get_secret_value())
        self.service_key = {"X-Service-Key": key.get_secret_value()}
        self.telegram = TelegramPort()  # replies of the bot to commands
        self.notifications = TelegramPort()  # messages sent by the notifier
        self.bot = build_bot_command(self.automation_db, self.gateway, self.telegram)
        self.update_id = 1_000_000
        self.clients: list[httpx.AsyncClient] = []
        self.consumer: asyncio.Task[None] | None = None
        await self.wipe()
        self.addAsyncCleanup(self.wipe)
        self.addAsyncCleanup(self.stop_consumer)

    # ---- helpers ------------------------------------------------------------

    async def wipe(self) -> None:
        """Remove everything this class created; safe to run twice."""
        await self.settle_outbox()
        async with self.identity_db.sessions() as session:
            ids = "(SELECT id FROM members WHERE telegram_id BETWEEN :lo AND :hi OR "
            ids += "telegram_id BETWEEN :alo AND :ahi)"
            params = {
                "lo": USER_FIRST,
                "hi": USER_LAST,
                "alo": ADMIN_RANGE[0],
                "ahi": ADMIN_RANGE[1],
            }
            await session.execute(
                text(f"UPDATE members SET decided_by = NULL WHERE id IN {ids}"), params
            )
            await session.execute(text("DELETE FROM outbox"))
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
            await session.execute(text("DELETE FROM notification_intents"))
            await session.execute(text("DELETE FROM inbox_messages"))
            await session.execute(text("DELETE FROM telegram_updates"))
            await session.commit()
        await self.clear_broker_state()

    async def clear_broker_state(self) -> None:
        async with open_resources(Settings(service="automation")) as resources:
            channel = await resources.rabbitmq.channel()
            _, queue = await declare_identity_events(channel)
            await queue.purge()
            assert resources.redis is not None
            await resources.redis.delete(NOTIFICATION_STREAM)

    async def settle_outbox(self) -> None:
        """Wait until identity-worker has nothing due, so no publish races a wipe."""
        for _ in range(100):
            async with self.identity_db.sessions() as session:
                due = await session.scalar(
                    text(
                        "SELECT count(*) FROM outbox WHERE published_at IS NULL "
                        "AND next_attempt_at IS NOT NULL"
                    )
                )
            if not due:
                return
            await asyncio.sleep(0.3)
        self.fail("identity-worker did not publish the outbox")

    async def sql(self, statement: str, **params: object) -> list[tuple]:
        async with self.identity_db.sessions() as session:
            result = await session.execute(text(statement), params)
            return [tuple(row) for row in result]

    async def put_member(self, telegram_id: int, role: str, status: str) -> UUID:
        member_id = uuid4()
        async with self.identity_db.sessions() as session:
            await session.execute(
                text(
                    "INSERT INTO members (id, telegram_id, role, status, created_at) "
                    "VALUES (:id, :tg, :role, :status, now())"
                ),
                {"id": member_id, "tg": telegram_id, "role": role, "status": status},
            )
            await session.commit()
        return member_id

    async def member(self, telegram_id: int) -> tuple:
        rows = await self.sql(
            "SELECT id, role, status, access_version FROM members "
            "WHERE telegram_id = :tg",
            tg=telegram_id,
        )
        self.assertEqual(len(rows), 1)
        return rows[0]

    async def send(self, telegram_id: int, command: str) -> str:
        self.update_id += 1
        before = len(self.telegram.messages)
        await self.bot.execute(
            HandleBotCommandDto(
                update_id=self.update_id,
                telegram_id=telegram_id,
                chat_id=telegram_id,
                text=command,
                private=True,
                display_name=f"Smoke {telegram_id}",
            )
        )
        self.assertEqual(len(self.telegram.messages), before + 1)
        chat, message = self.telegram.messages[-1]
        self.assertEqual(chat, telegram_id)
        return message

    async def login_code(self, telegram_id: int) -> str:
        message = await self.send(telegram_id, "/login")
        matched = re.search(r"Код входа: ([A-Za-z0-9_-]{32})\n", message)
        self.assertIsNotNone(matched, "the bot must deliver a login code")
        assert matched is not None
        return matched.group(1)

    async def login(self, telegram_id: int) -> httpx.AsyncClient:
        """A browser-like client holding a fresh session cookie."""
        client = httpx.AsyncClient(base_url=IDENTITY_URL, timeout=10)
        self.clients.append(client)
        self.addAsyncCleanup(client.aclose)
        exchanged = await client.post(
            "/api/v1/auth/exchange",
            json={"code": await self.login_code(telegram_id)},
            headers=ORIGIN,
        )
        self.assertEqual(exchanged.status_code, 200)
        return client

    async def decide(
        self,
        admin: httpx.AsyncClient,
        member_id: UUID,
        action: str,
        version: int,
    ) -> httpx.Response:
        return await admin.post(
            f"/api/v1/admin/members/{member_id}/{action}",
            json={"expected_version": version},
            headers=ORIGIN,
        )

    async def new_admin(self, telegram_id: int) -> tuple[UUID, httpx.AsyncClient]:
        member_id = await self.put_member(telegram_id, "admin", "approved")
        return member_id, await self.login(telegram_id)

    async def applicant(self, telegram_id: int) -> UUID:
        await self.send(telegram_id, "/join")
        return (await self.member(telegram_id))[0]

    def start_pipeline(self):  # type: ignore[no-untyped-def]
        async def build() -> object:
            resources = await self.stack.enter_async_context(
                open_resources(Settings(service="automation"))
            )
            pipeline = build_notifications(
                resources,
                self.gateway,
                self.notifications,
                consumer_name=f"smoke-{uuid4().hex}",
                stuck_after_ms=1_000,
            )
            await pipeline.queue.ensure_group()
            self.consumer = asyncio.create_task(pipeline.consumer.run())
            return pipeline

        return build()

    async def stop_consumer(self) -> None:
        if self.consumer is not None:
            self.consumer.cancel()
            await asyncio.gather(self.consumer, return_exceptions=True)
            self.consumer = None

    async def intents(self, chat_id: int) -> list[tuple]:
        async with self.automation_db.sessions() as session:
            result = await session.execute(
                text(
                    "SELECT kind, status, access_version FROM notification_intents "
                    "WHERE chat_id = :chat ORDER BY created_at"
                ),
                {"chat": chat_id},
            )
            return [tuple(row) for row in result]

    async def pump(self, pipeline, done) -> None:  # type: ignore[no-untyped-def]
        """Drive the Automation steps until `done()` holds."""
        for _ in range(150):
            await pipeline.relay.execute()
            await pipeline.notifier.execute(block_ms=100)
            if await done():
                return
            await asyncio.sleep(0.2)
        self.fail("the notification did not settle in time")

    # ---- join and decisions -------------------------------------------------

    async def test_join_approval_and_login_code(self) -> None:
        user = USER_FIRST
        _, admin = await self.new_admin(ADMIN_A)
        self.assertIn("/join", await self.send(user, "/start"))
        self.assertIn("Доступ не предоставлен", await self.send(user, "/login"))

        self.assertIn("Заявка отправлена", await self.send(user, "/join"))

        member_id, role, status, version = await self.member(user)
        self.assertEqual((role, status, version), ("user", "pending", 1))
        self.assertIn("Доступ не предоставлен", await self.send(user, "/login"))
        denied = await self.internal.post(
            "/internal/v1/login-codes",
            json={"telegram_id": user},
            headers=self.service_key,
        )
        self.assertEqual(denied.status_code, 403)
        waiting = await admin.get("/api/v1/admin/join-requests")
        self.assertEqual(waiting.status_code, 200)
        self.assertEqual([m["telegram_id"] for m in waiting.json()], [user])

        approved = await self.decide(admin, member_id, "approve", 1)

        self.assertEqual(approved.status_code, 200)
        self.assertTrue(approved.json()["changed"])
        self.assertEqual(approved.json()["member"]["status"], "approved")
        session_client = await self.login(user)
        current = await session_client.get("/api/v1/users/me")
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json()["role"], "user")
        listed = await admin.get("/api/v1/admin/members")
        self.assertIn(user, [m["telegram_id"] for m in listed.json()])

    async def test_reject_then_reapply_and_blocked_cannot_bypass(self) -> None:
        user = USER_FIRST + 1
        _, admin = await self.new_admin(ADMIN_A)
        member_id = await self.applicant(user)

        rejected = await self.decide(admin, member_id, "reject", 1)
        self.assertEqual(rejected.json()["member"]["status"], "rejected")
        self.assertIn("Заявка отправлена", await self.send(user, "/join"))
        self.assertEqual((await self.member(user))[2:], ("pending", 3))

        blocked = await self.decide(admin, member_id, "block", 3)
        self.assertEqual(blocked.status_code, 200)
        self.assertIn("заблокирован", await self.send(user, "/join"))
        self.assertEqual((await self.member(user))[2:], ("blocked", 4))
        self.assertIn("Доступ не предоставлен", await self.send(user, "/login"))

    async def test_join_requests_are_limited_per_telegram_id(self) -> None:
        user = USER_FIRST + 2
        for _ in range(5):
            await self.send(user, "/join")

        limited = await self.send(user, "/join")
        direct = await self.internal.post(
            "/internal/v1/join-requests",
            json={"telegram_id": user},
            headers=self.service_key,
        )
        other = await self.send(user + 1, "/join")

        self.assertIn("Слишком много запросов", limited)
        self.assertEqual(direct.status_code, 429)
        self.assertEqual(direct.headers["retry-after"], "60")
        self.assertIn("Заявка отправлена", other)

    async def test_permissions_and_origin(self) -> None:
        user = USER_FIRST + 3
        member_id = await self.applicant(user)
        await self.put_member(USER_FIRST + 4, "user", "approved")
        plain_user = await self.login(USER_FIRST + 4)
        _, admin = await self.new_admin(ADMIN_A)
        anonymous = httpx.AsyncClient(base_url=IDENTITY_URL, timeout=10)
        self.addAsyncCleanup(anonymous.aclose)
        action = f"/api/v1/admin/members/{member_id}/approve"
        body = {"expected_version": 1}

        self.assertEqual(
            (await anonymous.get("/api/v1/admin/members")).status_code, 401
        )
        self.assertEqual(
            (await anonymous.post(action, json=body, headers=ORIGIN)).status_code, 401
        )
        forbidden = await plain_user.get("/api/v1/admin/join-requests")
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(forbidden.json(), {"detail": "admin_required"})
        self.assertEqual(
            (await plain_user.post(action, json=body, headers=ORIGIN)).status_code, 403
        )
        foreign = await admin.post(
            action, json=body, headers={"Origin": "https://foreign.example"}
        )
        self.assertEqual(foreign.status_code, 403)
        self.assertEqual(foreign.json(), {"detail": "origin_not_allowed"})
        self.assertEqual((await self.member(user))[2:], ("pending", 1))
        # The service key is not an admin credential, and a wrong one is refused.
        self.assertEqual(
            (await admin.get("/internal/v1/members/by-telegram/1")).status_code, 401
        )
        wrong = await self.internal.post(
            "/internal/v1/join-requests",
            json={"telegram_id": user},
            headers={"X-Service-Key": "wrong"},
        )
        self.assertEqual(wrong.status_code, 401)
        hidden = await self.browser.post(
            "/internal/v1/join-requests", json={"telegram_id": user}
        )
        self.assertEqual(hidden.status_code, 404)

    async def test_repeats_and_stale_versions_have_no_new_effect(self) -> None:
        user = USER_FIRST + 5
        _, admin_a = await self.new_admin(ADMIN_A)
        _, admin_b = await self.new_admin(ADMIN_B)
        member_id = await self.applicant(user)

        first, second = await asyncio.gather(
            self.decide(admin_a, member_id, "approve", 1),
            self.decide(admin_b, member_id, "approve", 1),
        )

        self.assertEqual([first.status_code, second.status_code], [200, 200])
        self.assertEqual(
            sorted([first.json()["changed"], second.json()["changed"]]), [False, True]
        )
        self.assertEqual((await self.member(user))[2:], ("approved", 2))
        self.assertEqual(
            await self.sql(
                "SELECT count(*) FROM outbox WHERE payload->>'telegram_id' = :t",
                t=str(user),
            ),
            [(1,)],
        )
        repeat = await self.decide(admin_a, member_id, "approve", 1)
        self.assertEqual(repeat.json()["changed"], False)
        conflict = await self.decide(admin_a, member_id, "reject", 1)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json(), {"detail": "version_conflict"})
        invalid = await self.decide(admin_a, member_id, "reject", 2)
        self.assertEqual(invalid.json(), {"detail": "invalid_transition"})
        self.assertEqual((await self.member(user))[2:], ("approved", 2))
        self.assertEqual(
            await self.sql(
                "SELECT count(*) FROM outbox WHERE payload->>'telegram_id' = :t",
                t=str(user),
            ),
            [(1,)],
        )

    async def test_self_block_and_concurrent_mutual_blocks_keep_an_admin(self) -> None:
        id_a, admin_a = await self.new_admin(ADMIN_A)
        id_b, admin_b = await self.new_admin(ADMIN_B)

        own = await self.decide(admin_a, id_a, "block", 1)
        self.assertEqual(
            (own.status_code, own.json()), (409, {"detail": "cannot_block_self"})
        )
        self.assertEqual((await self.member(ADMIN_A))[2:], ("approved", 1))

        for round_number in range(3):
            current = {
                ADMIN_A: (await self.member(ADMIN_A))[3],
                ADMIN_B: (await self.member(ADMIN_B))[3],
            }
            first, second = await asyncio.gather(
                self.decide(admin_a, id_b, "block", current[ADMIN_B]),
                self.decide(admin_b, id_a, "block", current[ADMIN_A]),
            )
            codes = sorted([first.status_code, second.status_code])
            self.assertEqual(codes[0], 200, f"round {round_number}: one decision wins")
            loser = first if first.status_code != 200 else second
            self.assertIn(loser.status_code, (401, 409))
            if loser.status_code == 409:
                self.assertEqual(loser.json(), {"detail": "last_admin"})
            approved_admins = await self.sql(
                "SELECT count(*) FROM members WHERE role = 'admin' AND "
                "status = 'approved' AND telegram_id IN (:a, :b)",
                a=ADMIN_A,
                b=ADMIN_B,
            )
            self.assertEqual(approved_admins, [(1,)], f"round {round_number}")
            # Restore both admins for the next round with fresh sessions.
            blocked_tg = (
                ADMIN_B if (await self.member(ADMIN_B))[2] == "blocked" else ADMIN_A
            )
            winner_tg = ADMIN_A if blocked_tg == ADMIN_B else ADMIN_B
            winner = admin_a if winner_tg == ADMIN_A else admin_b
            blocked_id, blocked_version = (
                (await self.member(blocked_tg))[0],
                (await self.member(blocked_tg))[3],
            )
            restored = await self.decide(winner, blocked_id, "unblock", blocked_version)
            self.assertEqual(restored.status_code, 200)
            admin_a = await self.login(ADMIN_A)
            admin_b = await self.login(ADMIN_B)

    async def test_block_revokes_sessions_and_unblock_needs_a_fresh_login(self) -> None:
        user = USER_FIRST + 6
        _, admin = await self.new_admin(ADMIN_A)
        member_id = await self.applicant(user)
        await self.decide(admin, member_id, "approve", 1)
        member_client = await self.login(user)
        cookie = member_client.cookies["botflow_session"]
        pending_code = await self.login_code(user)
        self.assertEqual((await member_client.get("/api/v1/users/me")).status_code, 200)

        blocked = await self.decide(admin, member_id, "block", 2)

        self.assertEqual(blocked.status_code, 200)
        self.assertEqual((await member_client.get("/api/v1/users/me")).status_code, 401)
        self.assertEqual(
            await self.sql(
                "SELECT count(*) FROM auth_sessions WHERE member_id = :id "
                "AND revoked_at IS NULL",
                id=member_id,
            ),
            [(0,)],
        )
        spent = await self.internal.post(
            "/api/v1/auth/exchange", json={"code": pending_code}, headers=ORIGIN
        )
        self.assertEqual(spent.status_code, 401)  # the live code was cancelled
        self.assertIn("Доступ не предоставлен", await self.send(user, "/login"))

        unblocked = await self.decide(admin, member_id, "unblock", 3)

        self.assertEqual(unblocked.status_code, 200)
        stale = await self.internal.get(
            "/api/v1/users/me", headers={"Cookie": f"botflow_session={cookie}"}
        )
        self.assertEqual(stale.status_code, 401)  # unblock does not revive sessions
        fresh = await self.login(user)
        self.assertEqual((await fresh.get("/api/v1/users/me")).status_code, 200)

    async def test_planted_fsm_state_grants_nothing(self) -> None:
        user = USER_FIRST + 7
        member_id = await self.applicant(user)
        async with open_resources(Settings(service="automation")) as resources:
            assert resources.redis is not None
            await resources.redis.set(
                f"botflow:fsm:{user}:{user}:state", "approved:admin"
            )
            try:
                reply = await self.send(user, "/login")
                start = await self.send(user, "/start")
            finally:
                await resources.redis.delete(f"botflow:fsm:{user}:{user}:state")

        self.assertIn("Доступ не предоставлен", reply)
        self.assertIn("на рассмотрении", start)
        self.assertEqual((await self.member(user))[2:], ("pending", 1))
        self.assertEqual(
            await self.sql(
                "SELECT count(*) FROM login_codes WHERE member_id = :id", id=member_id
            ),
            [(0,)],
        )

    # ---- notifications ------------------------------------------------------

    async def test_decisions_reach_telegram_once_through_both_brokers(self) -> None:
        approved_user, rejected_user = USER_FIRST + 8, USER_FIRST + 9
        _, admin = await self.new_admin(ADMIN_A)
        approved_id = await self.applicant(approved_user)
        rejected_id = await self.applicant(rejected_user)
        pipeline = await self.start_pipeline()

        await self.decide(admin, approved_id, "approve", 1)
        await self.decide(admin, rejected_id, "reject", 1)

        async def delivered() -> bool:
            return bool(
                self.notifications.to(approved_user)
                and self.notifications.to(rejected_user)
            )

        await self.pump(pipeline, delivered)
        self.assertEqual(self.notifications.to(approved_user)[-1], APPROVED_TEXT)
        self.assertEqual(self.notifications.to(rejected_user)[-1], REJECTED_TEXT)
        self.assertEqual(
            await self.intents(approved_user), [("access.approved", "sent", 2)]
        )
        self.assertEqual(
            await self.sql("SELECT count(*) FROM outbox WHERE published_at IS NULL"),
            [(0,)],
        )
        async with open_resources(Settings(service="automation")) as resources:
            assert resources.redis is not None
            pending = await resources.redis.xpending(NOTIFICATION_STREAM, "notifier")
            self.assertEqual(pending["pending"], 0)

            # The broker may deliver the same event again: nothing new happens.
            (row,) = await self.sql(
                "SELECT id, payload FROM outbox WHERE payload->>'telegram_id' = :t",
                t=str(approved_user),
            )
            await ConfirmedPublisher(resources.rabbitmq).publish(
                ACCESS_DECIDED_ROUTING_KEY,
                json.dumps(row[1]).encode(),
                message_id=str(row[0]),
                type=ACCESS_DECIDED_ROUTING_KEY,
            )
            for _ in range(10):
                await pipeline.relay.execute()
                await pipeline.notifier.execute(block_ms=100)
                await asyncio.sleep(0.2)
        self.assertEqual(len(await self.intents(approved_user)), 1)
        self.assertEqual(len(self.notifications.to(approved_user)), 1)
        channel_queue = await self.queue_depth()
        self.assertEqual(channel_queue, 0)

    async def queue_depth(self) -> int:
        async with open_resources(Settings(service="automation")) as resources:
            channel = await resources.rabbitmq.channel()
            queue = await channel.declare_queue(AUTOMATION_IDENTITY_QUEUE, durable=True)
            return queue.declaration_result.message_count or 0

    async def test_a_decision_made_stale_before_delivery_is_never_sent(self) -> None:
        user = USER_FIRST + 10
        _, admin = await self.new_admin(ADMIN_A)
        member_id = await self.applicant(user)
        await self.decide(admin, member_id, "approve", 1)
        await self.decide(admin, member_id, "block", 2)
        await self.settle_outbox()  # the approve event waits in RabbitMQ
        pipeline = await self.start_pipeline()

        async def settled() -> bool:
            rows = await self.intents(user)
            return bool(rows) and rows[0][1] not in {"pending", "enqueued"}

        await self.pump(pipeline, settled)

        self.assertEqual(
            await self.intents(user), [("access.approved", "suppressed_stale", 2)]
        )
        self.assertEqual(self.notifications.to(user), [])
        # A block itself sends nothing to the blocked author.
        self.assertEqual(
            await self.sql(
                "SELECT count(*) FROM outbox WHERE payload->>'decision' = 'blocked'"
            ),
            [(0,)],
        )

    async def test_final_notification_to_a_blocked_author_is_suppressed(self) -> None:
        user = USER_FIRST + 11
        _, admin = await self.new_admin(ADMIN_A)
        member_id = await self.applicant(user)
        await self.decide(admin, member_id, "approve", 1)
        await self.decide(admin, member_id, "block", 2)
        pipeline = await self.start_pipeline()
        intent_id = uuid4()
        async with self.automation_db.sessions() as session:
            await session.execute(
                text(
                    "INSERT INTO notification_intents (id, chat_id, member_id, kind, "
                    "access_version, status, created_at, updated_at) VALUES "
                    "(:id, :chat, :member, 'task.result', 1, 'pending', now(), now())"
                ),
                {"id": intent_id, "chat": user, "member": member_id},
            )
            await session.commit()

        async def settled() -> bool:
            rows = await self.intents(user)
            return any(
                kind == "task.result" and status != "pending" and status != "enqueued"
                for kind, status, _ in rows
            )

        await self.pump(pipeline, settled)

        results = [r for r in await self.intents(user) if r[0] == "task.result"]
        self.assertEqual(results, [("task.result", "suppressed_blocked", 1)])
        self.assertEqual(self.notifications.to(user), [])
