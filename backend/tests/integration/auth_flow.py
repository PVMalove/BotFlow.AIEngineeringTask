"""Real Identity HTTP/PG with Telegram substituted only at the Messenger port."""

import asyncio
import hashlib
import os
import re
import unittest
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path

import httpx
from api.containers import build_bot_command
from application.commands.handle_bot_command import HandleBotCommandDto
from application.ports import IdentityUnavailable
from botflow_platform.resources import open_database
from botflow_platform.settings import Settings
from infrastructure.identity_gateway import HttpIdentityGateway
from sqlalchemy import text

ADMIN = int(os.environ["INITIAL_ADMIN_TELEGRAM_ID"])
ORIGIN = {"Origin": "http://frontend:8080"}


class TelegramPort:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send(self, chat_id: int, message: str) -> None:
        self.messages.append((chat_id, message))


class AuthFlow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.stack = AsyncExitStack()
        self.addAsyncCleanup(self.stack.aclose)
        self.identity_db = await self.stack.enter_async_context(
            open_database(Settings(service="identity"))
        )
        automation = await self.stack.enter_async_context(
            open_database(Settings(service="automation"))
        )
        self.internal = await self.stack.enter_async_context(
            httpx.AsyncClient(base_url="http://identity-api:8000", timeout=10)
        )
        self.browser = await self.stack.enter_async_context(
            httpx.AsyncClient(base_url="http://frontend:8080", timeout=10)
        )
        key = Settings(service="automation").secret("automation_service_key")
        self.headers = {"X-Service-Key": key.get_secret_value()}
        self.telegram = TelegramPort()
        self.gateway = HttpIdentityGateway(self.internal, key.get_secret_value())
        self.bot = build_bot_command(automation, self.gateway, self.telegram)
        self.update_id = 0
        # All data belongs to this UUID-owned project. Each test starts independently.
        async with self.identity_db.sessions() as session:
            await session.execute(text("DELETE FROM rate_limits"))
            await session.execute(text("DELETE FROM auth_sessions"))
            await session.execute(text("DELETE FROM login_codes"))
            await session.commit()
        async with automation.sessions() as session:
            await session.execute(text("DELETE FROM telegram_updates"))
            await session.commit()

    async def code(self) -> str:
        self.update_id += 1
        await self.bot.execute(
            HandleBotCommandDto(
                update_id=self.update_id,
                telegram_id=ADMIN,
                chat_id=ADMIN,
                text="/login",
                private=True,
            )
        )
        chat, message = self.telegram.messages[-1]
        self.assertEqual(chat, ADMIN)
        matched = re.search(r"Код входа: ([A-Za-z0-9_-]{32})\n", message)
        self.assertIsNotNone(matched, "Bot must deliver a login code")
        assert matched is not None
        return matched.group(1)

    async def exchange(self, code: str) -> httpx.Response:
        return await self.browser.post(
            "/api/v1/auth/exchange", json={"code": code}, headers=ORIGIN
        )

    async def test_bot_code_exchange_current_member_and_revocation(self) -> None:
        await self.bot.execute(
            update_id=100,
            telegram_id=ADMIN,
            chat_id=ADMIN,
            text="/start",
            private=True,
        )
        self.assertEqual(
            self.telegram.messages,
            [(ADMIN, "Для входа в веб-панель отправьте /login.")],
        )
        code = await self.code()
        await self.bot.execute(
            update_id=1,
            telegram_id=ADMIN,
            chat_id=ADMIN,
            text="/login",
            private=True,
        )
        self.assertEqual(len(self.telegram.messages), 2)
        digest = hashlib.sha256(code.encode()).hexdigest()
        async with self.identity_db.sessions() as session:
            row = (
                await session.execute(
                    text("SELECT code_hash, created_at, expires_at FROM login_codes")
                )
            ).one()
            self.assertEqual(row.code_hash, digest)
            self.assertNotEqual(row.code_hash, code)
            self.assertEqual(row.expires_at - row.created_at, timedelta(minutes=5))

        denied_origin = await self.browser.post(
            "/api/v1/auth/exchange",
            json={"code": code},
            headers={"Origin": "https://foreign.example"},
        )
        self.assertEqual(denied_origin.status_code, 403)
        exchanged = await self.exchange(code)
        self.assertEqual(exchanged.status_code, 200)
        cookie = exchanged.headers["set-cookie"]
        for attribute in ("HttpOnly", "SameSite=strict", "Max-Age=28800", "Path=/"):
            self.assertIn(attribute, cookie)
        token = self.browser.cookies["botflow_session"]
        current = await self.browser.get("/api/v1/users/me")
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json()["telegram_id"], ADMIN)
        self.assertEqual(current.json()["role"], "admin")
        self.assertEqual((await self.exchange(code)).status_code, 401)

        logout = await self.browser.post("/api/v1/auth/logout", headers=ORIGIN)
        self.assertEqual(logout.status_code, 204)
        self.assertIn("Max-Age=0", logout.headers["set-cookie"])
        revoked = await self.browser.get(
            "/api/v1/users/me", headers={"Cookie": f"botflow_session={token}"}
        )
        self.assertEqual(revoked.status_code, 401)

    async def test_new_code_cancels_old_and_expired_code_cannot_log_in(self) -> None:
        previous = await self.code()
        latest = await self.code()
        self.assertNotEqual(previous, latest)
        self.assertEqual((await self.exchange(previous)).status_code, 401)
        async with self.identity_db.sessions() as session:
            await session.execute(
                text(
                    "UPDATE login_codes SET expires_at = created_at "
                    "WHERE code_hash = :hash"
                ),
                {"hash": hashlib.sha256(latest.encode()).hexdigest()},
            )
            await session.commit()

        self.assertEqual((await self.exchange(latest)).status_code, 401)
        self.assertEqual((await self.exchange("x" * 32)).status_code, 401)
        self.assertNotIn("botflow_session", self.browser.cookies)

    async def test_two_concurrent_exchanges_create_exactly_one_session(self) -> None:
        code = await self.code()

        responses = await asyncio.gather(self.exchange(code), self.exchange(code))

        self.assertEqual(
            sorted(response.status_code for response in responses), [200, 401]
        )
        async with self.identity_db.sessions() as session:
            self.assertEqual(
                await session.scalar(text("SELECT count(*) FROM auth_sessions")), 1
            )

    async def test_concurrent_issuance_keeps_only_the_newest_code_live(self) -> None:
        issued = await asyncio.gather(
            *(
                self.internal.post(
                    "/internal/v1/login-codes",
                    json={"telegram_id": ADMIN},
                    headers=self.headers,
                )
                for _ in range(2)
            )
        )
        self.assertEqual([response.status_code for response in issued], [200, 200])

        responses = await asyncio.gather(
            *(self.exchange(response.json()["code"]) for response in issued)
        )

        self.assertEqual(
            sorted(response.status_code for response in responses), [200, 401]
        )

    async def test_postgres_issue_and_exchange_counters_enforce_limits(self) -> None:
        # Seed adjacent windows to avoid dependence on the minute boundary.
        async with self.identity_db.sessions() as session:
            await session.execute(
                text("""
                INSERT INTO rate_limits (key, window_start, count)
                SELECT :key,
                    date_trunc('minute', now()) + n * interval '1 minute', :count
                FROM generate_series(0, 1) AS n
            """),
                {"key": f"issue:{ADMIN}", "count": 5},
            )
            # Discover the ingress peer key via one wrong exchange.
            await session.commit()
        wrong = await self.exchange("x" * 32)
        self.assertEqual(wrong.status_code, 401)
        async with self.identity_db.sessions() as session:
            key = await session.scalar(
                text("SELECT key FROM rate_limits WHERE key LIKE 'exchange:%'")
            )
            await session.execute(
                text("DELETE FROM rate_limits WHERE key = :key"), {"key": key}
            )
            await session.execute(
                text("""
                INSERT INTO rate_limits (key, window_start, count)
                SELECT :key,
                    date_trunc('minute', now()) + n * interval '1 minute', 30
                FROM generate_series(0, 1) AS n
            """),
                {"key": key},
            )
            await session.commit()

        issued = await self.internal.post(
            "/internal/v1/login-codes",
            json={"telegram_id": ADMIN},
            headers=self.headers,
        )
        exchanged = await self.exchange("x" * 32)

        self.assertEqual(issued.status_code, 429)
        self.assertEqual(exchanged.status_code, 429)
        self.assertEqual(exchanged.headers["retry-after"], "60")

    async def test_keys_and_internal_routes_are_isolated_from_the_browser(self) -> None:
        self.assertFalse(Path("/run/secrets/identity_jwt_private_key").exists())
        hidden = await self.browser.get(
            f"/internal/v1/members/by-telegram/{ADMIN}", headers=self.headers
        )
        no_key = await self.internal.get(f"/internal/v1/members/by-telegram/{ADMIN}")
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(no_key.status_code, 401)
        with self.assertRaises(IdentityUnavailable):
            await HttpIdentityGateway(
                self.internal, "wrong-service-key"
            ).issue_login_code(ADMIN)

    async def asyncTearDown(self) -> None:
        # InfrastructureSmoke checks that the migrated inbox is initially empty.
        async with open_database(Settings(service="automation")) as database:
            async with database.sessions() as session:
                await session.execute(text("DELETE FROM telegram_updates"))
                await session.commit()
