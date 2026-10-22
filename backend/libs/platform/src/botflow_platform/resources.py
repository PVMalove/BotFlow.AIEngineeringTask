"""Process-owned connections; operation-owned sessions with rollback on close."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any

import aioboto3  # type: ignore[import-untyped]
from aio_pika import connect_robust
from aio_pika.abc import AbstractRobustConnection
from botocore.config import Config  # type: ignore[import-untyped]
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from botflow_platform.settings import Settings


@dataclass
class Resources:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    rabbitmq: AbstractRobustConnection
    redis: Redis | None = None
    s3: Any = None
    closed: bool = False


@dataclass
class Database:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]


def _create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url(),
        pool_pre_ping=True,
        connect_args={"timeout": 10},
        hide_parameters=True,
    )


@asynccontextmanager
async def open_database(settings: Settings) -> AsyncIterator[Database]:
    """Own database only, for CLI processes that need no broker or cache."""
    engine = _create_engine(settings)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        await engine.dispose()
        # Driver exceptions can contain credential-bearing connection arguments.
        raise RuntimeError(
            f"Database initialization failed for {settings.service}"
        ) from None
    try:
        yield Database(engine, async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


def _failed(settings: Settings) -> RuntimeError:
    # Driver and SDK exceptions can contain credential-bearing connection arguments.
    return RuntimeError(f"Infrastructure initialization failed for {settings.service}")


@asynccontextmanager
async def open_redis(settings: Settings) -> AsyncIterator[Redis]:
    """The service's own Redis user; for Tasks and Automation only."""
    redis = Redis(
        host=settings.redis_host,
        username=settings.service,
        password=settings.secret(
            f"redis_{settings.service}_password"
        ).get_secret_value(),
        socket_connect_timeout=10,
        socket_timeout=10,
    )
    try:
        await redis.ping()
    except Exception:
        await redis.aclose()
        raise _failed(settings) from None
    try:
        yield redis
    finally:
        await redis.aclose()


@asynccontextmanager
async def open_s3(settings: Settings) -> AsyncIterator[Any]:
    """The S3 client of the audio bucket; for Automation only."""
    async with AsyncExitStack() as stack:
        try:
            client = await stack.enter_async_context(
                aioboto3.Session().client(
                    "s3",
                    endpoint_url=settings.s3_endpoint,
                    region_name="us-east-1",
                    aws_access_key_id="botflow",
                    aws_secret_access_key=settings.secret(
                        "minio_password"
                    ).get_secret_value(),
                    config=Config(
                        connect_timeout=10,
                        read_timeout=10,
                        retries={"max_attempts": 2},
                        s3={"addressing_style": "path"},
                    ),
                )
            )
        except Exception:
            raise _failed(settings) from None
        yield client


@asynccontextmanager
async def open_resources(settings: Settings) -> AsyncIterator[Resources]:
    """Unwind every acquired resource even if a later connection fails."""
    async with AsyncExitStack() as stack:
        try:
            engine = _create_engine(settings)
            stack.push_async_callback(engine.dispose)
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            rabbitmq = await connect_robust(
                host=settings.rabbitmq_host,
                login="botflow",
                password=settings.secret("rabbitmq_password").get_secret_value(),
                timeout=10,
                fail_fast=True,
            )
            stack.push_async_callback(rabbitmq.close)
            resources = Resources(
                engine=engine,
                sessions=async_sessionmaker(engine, expire_on_commit=False),
                rabbitmq=rabbitmq,
            )
            if settings.service in {"tasks", "automation"}:
                resources.redis = await stack.enter_async_context(open_redis(settings))
            if settings.service == "automation":
                resources.s3 = await stack.enter_async_context(open_s3(settings))
        except RuntimeError:
            raise  # already sanitised by open_redis / open_s3
        except Exception:
            raise _failed(settings) from None
        try:
            yield resources
        finally:
            resources.closed = True
