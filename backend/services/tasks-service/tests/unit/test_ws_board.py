"""WS /ws/board: Origin and live Identity access decide every frame."""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from kernel_domain.errors import Error
from kernel_domain.result import Result
from kernel_platform.security.actor import Actor, ActorRole
from kernel_platform.security.errors import AuthErrors
from starlette.testclient import TestClient
from starlette.types import Message, Scope
from starlette.websockets import WebSocketDisconnect
from support.fakes import AUTHOR, FakeBoardFeed, TrackedApp, connected

from api.containers import Services
from api.dependencies import get_services
from api.endpoints import board_ws
from api.endpoints.board_ws import router
from api.main import create_tasks_app
from application.ports import BoardFeedUnavailable, IdentityUnavailable

TOKEN = "session-token"
HEADERS = {"origin": "http://testserver", "cookie": f"botflow_session={TOKEN}"}
SIGNAL = {"type": "board.changed"}
PING = {"type": "ping"}
POLICY_VIOLATION = 1008
TRY_AGAIN_LATER = 1013


class Identity:
    """Answers in turn: an `Error` refuses, `None` is Identity not answering,
    an `Actor` approves; after the script every session is approved."""

    def __init__(self, *answers: Actor | Error | None) -> None:
        self.answers = list(answers)
        self.tokens: list[str] = []

    async def require_approved(self, token: str) -> Result[Actor]:
        self.tokens.append(token)
        answer = self.answers.pop(0) if self.answers else Actor(AUTHOR, ActorRole.USER)
        if answer is None:
            raise IdentityUnavailable
        if isinstance(answer, Error):
            return Result[Actor].fail(answer)
        return Result[Actor].ok(answer)


class NoDatabase:
    """The WebSocket must not touch any database-backed handler."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the board socket used the database: {name}")


def services(identity: Identity, feed: FakeBoardFeed) -> Services:
    return Services(
        identity=identity,  # type: ignore[arg-type]
        list_tasks=NoDatabase(),  # type: ignore[arg-type]
        get_task=NoDatabase(),  # type: ignore[arg-type]
        change_task_status=NoDatabase(),  # type: ignore[arg-type]
        board_feed=feed,
        board_heartbeat=7.0,
    )


@pytest.fixture
def identity() -> Identity:
    return Identity()


@pytest.fixture
def feed() -> FakeBoardFeed:
    return FakeBoardFeed()


@pytest.fixture
def client(identity: Identity, feed: FakeBoardFeed) -> Iterator[TestClient]:
    app = create_tasks_app()

    async def override() -> Services:
        return services(identity, feed)

    app.dependency_overrides[get_services] = override
    yield TestClient(TrackedApp(app))


def refused_with(client: TestClient, headers: dict[str, str]) -> int:
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/ws/board", headers=headers):
            pass
    return refused.value.code


@pytest.mark.parametrize(
    "origin", [None, "", "http://evil.example", "https://testserver"]
)
def test_a_foreign_or_missing_origin_is_refused_before_identity_is_asked(
    client: TestClient, identity: Identity, feed: FakeBoardFeed, origin: str | None
) -> None:
    headers = {k: v for k, v in HEADERS.items() if k != "origin"}
    if origin is not None:
        headers["origin"] = origin

    assert refused_with(client, headers) == POLICY_VIOLATION

    assert identity.tokens == []
    assert feed.subscribed == 0


def test_a_missing_session_cookie_is_refused_before_identity_is_asked(
    client: TestClient, identity: Identity
) -> None:
    assert refused_with(client, {"origin": HEADERS["origin"]}) == POLICY_VIOLATION

    assert identity.tokens == []


@pytest.mark.parametrize(
    "error", [AuthErrors.unauthenticated(), AuthErrors.not_approved()]
)
def test_a_session_identity_refuses_is_refused(
    client: TestClient, identity: Identity, feed: FakeBoardFeed, error: Error
) -> None:
    identity.answers = [error]

    assert refused_with(client, HEADERS) == POLICY_VIOLATION

    assert identity.tokens == [TOKEN]
    assert feed.subscribed == 0


def test_identity_not_answering_on_connect_refuses_with_try_again_later(
    client: TestClient, identity: Identity, feed: FakeBoardFeed
) -> None:
    identity.answers = [None]

    assert refused_with(client, HEADERS) == TRY_AGAIN_LATER

    assert feed.subscribed == 0


def test_a_signal_reaches_an_approved_client_after_a_fresh_identity_check(
    client: TestClient, identity: Identity, feed: FakeBoardFeed
) -> None:
    feed.script = [True]

    with connected(client, HEADERS) as socket:
        assert socket.receive_json() == SIGNAL

    assert identity.tokens == [TOKEN, TOKEN]  # on connect and before the frame
    assert feed.unsubscribed == 1


def test_after_a_block_no_further_frame_is_sent_and_the_socket_closes(
    client: TestClient, identity: Identity, feed: FakeBoardFeed
) -> None:
    feed.script = [True, True]
    identity.answers = [
        Actor(AUTHOR, ActorRole.USER),
        Actor(AUTHOR, ActorRole.USER),
        AuthErrors.not_approved(),
    ]

    with connected(client, HEADERS) as socket:
        assert socket.receive_json() == SIGNAL
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()

    assert closed.value.code == POLICY_VIOLATION


def test_identity_not_answering_before_a_send_closes_without_a_frame(
    client: TestClient, identity: Identity, feed: FakeBoardFeed
) -> None:
    feed.script = [True]
    identity.answers = [Actor(AUTHOR, ActorRole.USER), None]

    with connected(client, HEADERS) as socket:
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()

    assert closed.value.code == TRY_AGAIN_LATER


def test_the_heartbeat_is_a_checked_frame_too(
    client: TestClient, identity: Identity, feed: FakeBoardFeed
) -> None:
    feed.script = [False, False]
    identity.answers = [
        Actor(AUTHOR, ActorRole.USER),
        Actor(AUTHOR, ActorRole.USER),
        AuthErrors.unauthenticated(),
    ]

    with connected(client, HEADERS) as socket:
        assert socket.receive_json() == PING
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()

    assert closed.value.code == POLICY_VIOLATION
    assert feed.timeouts == [7.0, 7.0]  # the interval Services configures
    assert identity.tokens == [TOKEN] * 3


def test_a_clients_departure_releases_the_subscription(
    client: TestClient, feed: FakeBoardFeed
) -> None:
    with connected(client, HEADERS):
        pass

    assert feed.subscribed == 1 and feed.unsubscribed == 1


def test_a_redis_that_cannot_subscribe_refuses_with_try_again_later(
    client: TestClient, identity: Identity, feed: FakeBoardFeed
) -> None:
    feed.subscribe_error = BoardFeedUnavailable()

    assert refused_with(client, HEADERS) == TRY_AGAIN_LATER


def test_a_redis_that_fails_while_listening_closes_with_try_again_later(
    client: TestClient, feed: FakeBoardFeed
) -> None:
    feed.script = [True, BoardFeedUnavailable()]

    with connected(client, HEADERS) as socket:
        assert socket.receive_json() == SIGNAL
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()

    assert closed.value.code == TRY_AGAIN_LATER
    assert feed.unsubscribed == 1


async def test_a_client_that_does_not_read_is_closed_with_try_again_later(
    monkeypatch: pytest.MonkeyPatch, identity: Identity, feed: FakeBoardFeed
) -> None:
    """Driven at the ASGI level: only there can a frame be made to stall."""
    monkeypatch.setattr(board_ws, "SEND_TIMEOUT", 0.05)
    feed.script = [True]
    app = create_tasks_app()

    async def override() -> Services:
        return services(identity, feed)

    app.dependency_overrides[get_services] = override
    scope: Scope = {
        "type": "websocket",
        "scheme": "ws",
        "path": "/ws/board",
        "query_string": b"",
        "root_path": "",
        "server": ("testserver", 80),
        "subprotocols": [],
        "headers": [(k.encode(), v.encode()) for k, v in HEADERS.items()]
        + [(b"host", b"testserver")],
    }
    sent: list[Message] = []
    inbox: list[Message] = [{"type": "websocket.connect"}]

    async def receive() -> Message:
        if inbox:
            return inbox.pop(0)
        await asyncio.Event().wait()  # the client says nothing and reads nothing
        raise AssertionError

    async def send(message: Message) -> None:
        if message["type"] == "websocket.send":
            await asyncio.sleep(30)  # the frame never reaches the client
        sent.append(message)

    await asyncio.wait_for(app(scope, receive, send), 2)

    closes = [m["code"] for m in sent if m["type"] == "websocket.close"]
    assert closes == [TRY_AGAIN_LATER]
    assert feed.unsubscribed == 1


def test_the_board_socket_depends_on_services_only_not_on_a_database_session() -> None:
    (route,) = router.routes
    dependencies = [d.call for d in route.dependant.dependencies]  # type: ignore[attr-defined]

    assert dependencies == [get_services]
