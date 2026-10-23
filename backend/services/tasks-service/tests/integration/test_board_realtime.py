"""The board signal and the board socket on a real Redis.

Runs only when BOTFLOW_TEST_REDIS_URL names a disposable Redis without ACL, e.g.
redis://localhost:6379/0. The tests only publish and subscribe on the board channel.
Pub/Sub is a hint to re-read the board, not a journal: nothing here depends on a
missed signal being replayed.
"""

import asyncio
import json
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
import redis as sync_redis
from conftest import AUTHOR, Board, FakeTasksUnitOfWork, TrackedApp, connected
from kernel_domain.errors import Error
from kernel_domain.result import Result
from kernel_platform.security.actor import Actor, ActorRole
from kernel_platform.security.errors import AuthErrors
from redis.asyncio import Redis
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.containers import Services
from api.dependencies import get_services
from api.main import create_tasks_app
from application.commands.create_task import (
    CreateTaskCommand,
    CreateTaskCommandHandler,
)
from application.ports import IdentityUnavailable
from domain.events.task_domain_event import TaskCreated
from domain.value_objects.task_id import TaskId
from infrastructure.board_signal import CHANNEL, RedisBoardFeed, RedisBoardSignal

REDIS_URL = os.environ.get("BOTFLOW_TEST_REDIS_URL")

pytestmark = pytest.mark.skipif(
    not REDIS_URL, reason="BOTFLOW_TEST_REDIS_URL is not set"
)

TOKEN = "session-token"
HEADERS = {"origin": "http://testserver", "cookie": f"botflow_session={TOKEN}"}
SIGNAL = {"type": "board.changed"}
PING = {"type": "ping"}
HEARTBEAT = 0.5
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class Identity:
    """Approves until told otherwise; the test flips it between signals."""

    def __init__(self) -> None:
        self.blocked = False
        self.unavailable = False

    async def require_approved(self, token: str) -> Result[Actor]:
        if self.unavailable:
            raise IdentityUnavailable
        if self.blocked:
            error: Error = AuthErrors.not_approved()
            return Result[Actor].fail(error)
        return Result[Actor].ok(Actor(AUTHOR, ActorRole.USER))


class NoDatabase:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the board socket used the database: {name}")


def command(**overrides: Any) -> CreateTaskCommand:
    values: dict[str, Any] = dict(
        message_id="m-1",
        correlation_id=uuid4(),
        bot_id=1,
        chat_id=2,
        source_message_id=3,
        text="Купить молоко",
        author_id=AUTHOR,
        author_telegram_id=2,
        author_name="Ann (@ann)",
    )
    values.update(overrides)
    return CreateTaskCommand(**values)


def publish_creation() -> None:
    """The signal of a creation, published the way the services publish it."""

    async def run() -> None:
        redis = Redis.from_url(REDIS_URL or "")
        try:
            await RedisBoardSignal(redis).handle(
                TaskCreated(
                    task_id=TaskId(uuid4()), correlation_id=uuid4(), source_key="1:2:3"
                )
            )
        finally:
            await redis.aclose()

    asyncio.run(run())


@pytest.fixture
def probe() -> Iterator[sync_redis.Redis]:
    client = sync_redis.Redis.from_url(REDIS_URL or "")
    yield client
    client.close()


def listeners(probe: sync_redis.Redis) -> int:
    (_, count), *_ = probe.pubsub_numsub(CHANNEL)
    return int(count)


@pytest.fixture
def identity() -> Identity:
    return Identity()


@pytest.fixture
def client(identity: Identity) -> Iterator[TestClient]:
    app = create_tasks_app()
    feeds: list[RedisBoardFeed] = []

    async def override() -> Services:
        if not feeds:  # created in the loop that serves the sockets
            feeds.append(RedisBoardFeed(Redis.from_url(REDIS_URL or "")))
        return Services(
            identity=identity,  # type: ignore[arg-type]
            list_tasks=NoDatabase(),  # type: ignore[arg-type]
            get_task=NoDatabase(),  # type: ignore[arg-type]
            change_task_status=NoDatabase(),  # type: ignore[arg-type]
            board_feed=feeds[0],
            board_heartbeat=HEARTBEAT,
        )

    app.dependency_overrides[get_services] = override
    with TestClient(TrackedApp(app)) as test_client:
        yield test_client


def next_board_frame(socket: Any) -> dict[str, Any]:
    """The next non-heartbeat frame; a heartbeat comes every HEARTBEAT seconds."""
    for _ in range(20):
        frame: dict[str, Any] = socket.receive_json()
        if frame != PING:
            return frame
    raise AssertionError("no board frame arrived")


async def test_a_created_task_publishes_one_signal_to_a_real_subscriber() -> None:
    redis = Redis.from_url(REDIS_URL or "")
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(CHANNEL)
        await pubsub.get_message(ignore_subscribe_messages=False, timeout=2)
        board = Board()
        sut = CreateTaskCommandHandler(
            lambda: FakeTasksUnitOfWork(board, [RedisBoardSignal(redis).handle]),
            lambda: NOW,
        )

        created = await sut.execute(command())
        again = await sut.execute(command())  # a redelivery of the same message

        assert created.is_ok and created.value is True
        assert again.is_ok and again.value is False
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
        assert message is not None
        (task,) = board.tasks.values()
        assert json.loads(message["data"]) == {
            "type": "board.changed",
            "task_id": str(task.id.value),
        }
        extra = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.3)
        assert extra is None  # the duplicate raised no second signal
    finally:
        await pubsub.aclose()  # type: ignore[no-untyped-call]
        await redis.aclose()


async def test_a_creation_with_an_unreachable_redis_still_succeeds() -> None:
    redis = Redis.from_url("redis://127.0.0.1:1/0")  # nothing listens here
    board = Board()
    sut = CreateTaskCommandHandler(
        lambda: FakeTasksUnitOfWork(board, [RedisBoardSignal(redis, timeout=1).handle]),
        lambda: NOW,
    )
    try:
        result = await sut.execute(command())
    finally:
        await redis.aclose()

    assert result.is_ok and result.value is True
    assert len(board.tasks) == 1


def test_a_foreign_origin_is_refused_and_nothing_is_subscribed(
    client: TestClient, probe: sync_redis.Redis
) -> None:
    before = listeners(probe)

    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect(
            "/ws/board", headers={**HEADERS, "origin": "http://evil.example"}
        ):
            pass

    assert refused.value.code == 1008
    assert listeners(probe) == before


def test_a_signal_from_redis_reaches_an_authorized_client(
    client: TestClient, probe: sync_redis.Redis
) -> None:
    before = listeners(probe)

    with connected(client, HEADERS) as socket:
        assert listeners(probe) == before + 1  # subscribed before the accept
        publish_creation()
        assert next_board_frame(socket) == SIGNAL

    deadline = time.monotonic() + 2
    while listeners(probe) != before and time.monotonic() < deadline:
        time.sleep(0.05)
    assert listeners(probe) == before  # the departed client released Redis


def test_after_a_block_the_next_signal_is_not_sent_and_the_socket_closes(
    client: TestClient, identity: Identity
) -> None:
    with connected(client, HEADERS) as socket:
        publish_creation()
        assert next_board_frame(socket) == SIGNAL

        identity.blocked = True
        publish_creation()
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()  # the first frame after the block is the close

    assert closed.value.code == 1008


def test_identity_going_away_closes_the_socket_without_a_frame(
    client: TestClient, identity: Identity
) -> None:
    with connected(client, HEADERS) as socket:
        identity.unavailable = True
        publish_creation()
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()

    assert closed.value.code == 1013


def test_identity_not_answering_on_connect_refuses_the_socket(
    client: TestClient, identity: Identity
) -> None:
    identity.unavailable = True

    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/ws/board", headers=HEADERS):
            pass

    assert refused.value.code == 1013
