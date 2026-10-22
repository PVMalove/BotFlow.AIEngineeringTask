"""Run only in the UUID-owned Compose project created by integration-smoke.py."""

import io
import logging
import os
import unittest
from uuid import UUID

import httpx
from access_flow import AccessFlow
from aio_pika import DeliveryMode, Message
from api.containers import Container as AutomationContainer
from auth_flow import AuthFlow
from kernel_platform.http import create_app
from kernel_platform.resources import open_database, open_resources
from kernel_platform.settings import Settings
from botocore.exceptions import ClientError
from sqlalchemy import text
from text_task_flow import TextTaskFlow
from voice_task_flow import VoiceTaskFlow

RUN_ID = UUID(os.environ["BOTFLOW_SMOKE_ID"]).hex
AFTER = os.environ["BOTFLOW_SMOKE_PHASE"] == "after"
TABLE = "botflow_smoke_" + RUN_ID
BUCKET = "botflow-smoke-" + RUN_ID
QUEUE = "botflow.smoke." + RUN_ID
ADMIN_TELEGRAM_ID = int(os.environ["INITIAL_ADMIN_TELEGRAM_ID"])
REDIS_KEY = "botflow:automation:smoke:" + RUN_ID
logging.disable(logging.CRITICAL)


class InfrastructureSmoke(unittest.IsolatedAsyncioTestCase):
    async def test_http_lifespan_closes_resources_on_exception(self) -> None:
        container = AutomationContainer()
        sut = create_app("automation", container)
        with self.assertRaisesRegex(RuntimeError, "smoke shutdown"):
            async with sut.router.lifespan_context(sut):
                resources = await container.resources()
                raise RuntimeError("smoke shutdown")
        self.assertTrue(resources.closed)
        self.assertTrue(resources.rabbitmq.is_closed)
        self.assertFalse(container.resources.initialized)

    async def test_all_databases_have_isolated_sessions_and_survive_restart(
        self,
    ) -> None:
        for service in ("identity", "tasks", "automation"):
            async with open_resources(Settings(service=service)) as resources:
                async with (
                    resources.sessions() as first,
                    resources.sessions() as second,
                ):
                    if not AFTER:
                        await first.execute(
                            text(f"CREATE TABLE {TABLE} (value integer)")
                        )
                        await first.execute(text(f"INSERT INTO {TABLE} VALUES (1)"))
                        await first.commit()
                        await first.execute(text(f"INSERT INTO {TABLE} VALUES (2)"))
                        first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
                        second_pid = await second.scalar(
                            text("SELECT pg_backend_pid()")
                        )
                        self.assertNotEqual(first_pid, second_pid)
                    self.assertEqual(
                        await second.scalar(text(f"SELECT count(*) FROM {TABLE}")), 1
                    )
                async with resources.sessions() as third:
                    self.assertEqual(
                        await third.scalar(text(f"SELECT count(*) FROM {TABLE}")), 1
                    )
                    if AFTER:
                        await third.execute(text(f"DROP TABLE {TABLE}"))
                        await third.commit()
            self.assertTrue(resources.closed)
            self.assertTrue(resources.rabbitmq.is_closed)

    async def test_migrations_and_admin_bootstrap_survive_repeat(self) -> None:
        for service, table in (
            ("identity", "members"),
            ("automation", "telegram_updates"),
        ):
            async with open_database(Settings(service=service)) as database:
                async with database.sessions() as session:
                    self.assertEqual(
                        await session.scalar(text(f"SELECT count(*) FROM {table}")),
                        1 if service == "identity" else 0,
                    )
        async with open_database(Settings(service="identity")) as database:
            async with database.sessions() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT role, status FROM members WHERE telegram_id = :id"
                        ),
                        {"id": ADMIN_TELEGRAM_ID},
                    )
                ).one()
                if not AFTER:
                    self.assertEqual(tuple(row), ("admin", "approved"))
                    await session.execute(text("UPDATE members SET status = 'blocked'"))
                    await session.commit()
                else:
                    # The repeated bootstrap after restart must not restore access.
                    self.assertEqual(tuple(row), ("admin", "blocked"))

    async def test_rabbitmq_durable_message_survives_restart(self) -> None:
        container = AutomationContainer()
        try:
            await container.init_resources()
            resources = await container.resources()
            async with resources.rabbitmq.channel(publisher_confirms=True) as channel:
                queue = await channel.declare_queue(QUEUE, durable=True)
                if AFTER:
                    message = await queue.get(timeout=10)
                    self.assertTrue(message.body == b"infrastructure-smoke")
                    await message.ack()
                    await queue.delete()
                else:
                    await channel.default_exchange.publish(
                        Message(
                            b"infrastructure-smoke",
                            delivery_mode=DeliveryMode.PERSISTENT,
                        ),
                        routing_key=QUEUE,
                        mandatory=True,
                    )
        finally:
            await container.shutdown_resources()

    async def test_redis_persisted_value_survives_restart(self) -> None:
        container = AutomationContainer()
        try:
            await container.init_resources()
            resources = await container.resources()
            self.assertIsNotNone(resources.redis)
            if AFTER:
                self.assertTrue(await resources.redis.get(REDIS_KEY) == b"smoke")
                await resources.redis.delete(REDIS_KEY)
            else:
                await resources.redis.set(REDIS_KEY, b"smoke")
                self.assertTrue(await resources.redis.get(REDIS_KEY) == b"smoke")
        finally:
            await container.shutdown_resources()

    async def test_private_s3_roundtrip_delete_and_restart_persistence(self) -> None:
        container = AutomationContainer()
        try:
            await container.init_resources()
            resources = await container.resources()
            s3 = resources.s3
            if not AFTER:
                await s3.create_bucket(Bucket=BUCKET)
                await s3.put_object(
                    Bucket=BUCKET, Key="roundtrip", Body=b"smoke-object"
                )
                response = await s3.get_object(Bucket=BUCKET, Key="roundtrip")
                async with response["Body"] as body:
                    self.assertTrue(await body.read() == b"smoke-object")
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"http://minio:9000/{BUCKET}/roundtrip")
                    self.assertEqual(response.status_code, 403)
                await s3.delete_object(Bucket=BUCKET, Key="roundtrip")
                with self.assertRaises(ClientError) as missing:
                    await s3.head_object(Bucket=BUCKET, Key="roundtrip")
                self.assertEqual(
                    missing.exception.response["ResponseMetadata"]["HTTPStatusCode"],
                    404,
                )
                await s3.put_object(
                    Bucket=BUCKET, Key="persistent", Body=b"smoke-persistent"
                )
            else:
                response = await s3.get_object(Bucket=BUCKET, Key="persistent")
                async with response["Body"] as body:
                    self.assertTrue(await body.read() == b"smoke-persistent")
                await s3.delete_object(Bucket=BUCKET, Key="persistent")
                await s3.delete_bucket(Bucket=BUCKET)
        finally:
            await container.shutdown_resources()

    async def test_http_liveness_does_not_claim_business_readiness(self) -> None:
        async with httpx.AsyncClient() as client:
            for service in ["identity", "tasks", "automation"]:
                live = await client.get(f"http://{service}-api:8000/health/live")
                self.assertEqual(live.status_code, 200)
                self.assertEqual(live.json(), {"status": "alive", "service": service})
                ready = await client.get(f"http://{service}-api:8000/health/ready")
                self.assertEqual(ready.status_code, 503)
            page = await client.get("http://frontend:8080/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("<html", page.text)


if __name__ == "__main__":
    suite = unittest.TestSuite()
    if not AFTER:
        # AccessFlow cleans up after itself; it runs first so that the shared
        # invariants checked below (one member, empty inbox) see the initial state.
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(AccessFlow))
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(AuthFlow))
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TextTaskFlow))
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(VoiceTaskFlow))
    suite.addTests(
        unittest.defaultTestLoader.loadTestsFromTestCase(InfrastructureSmoke)
    )
    result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    # Never print SDK exceptions, credentials, object payloads, or assertion operands.
    for test, _ in result.errors + result.failures:
        print(f"FAIL: {test.id()}")
    print(
        f"Resource checks: {result.testsRun}, "
        f"failures: {len(result.errors) + len(result.failures)}"
    )
    raise SystemExit(0 if result.wasSuccessful() and result.testsRun else 1)
