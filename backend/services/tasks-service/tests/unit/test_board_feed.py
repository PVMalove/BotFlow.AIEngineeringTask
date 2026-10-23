"""RedisBoardFeed: a subscription per client, signals folded, Redis failures typed."""

from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from application.ports import BoardFeedUnavailable
from infrastructure.board_signal import CHANNEL, RedisBoardFeed

CONFIRMATION = {"type": "subscribe", "channel": CHANNEL.encode(), "data": 1}
SIGNAL = {"type": "message", "channel": CHANNEL.encode(), "data": b"{}"}


class FakePubSub:
    """Hands out its script one message per `get_message`; None when it is over."""

    def __init__(self, *script: dict[str, Any] | Exception | None) -> None:
        self.script = list(script)
        self.subscribed: list[str] = []
        self.polls: list[tuple[bool, float | None]] = []
        self.closed = False
        self.subscribe_error: Exception | None = None

    async def subscribe(self, channel: str) -> None:
        if self.subscribe_error is not None:
            raise self.subscribe_error
        self.subscribed.append(channel)

    async def get_message(
        self, ignore_subscribe_messages: bool = False, timeout: float | None = 0.0
    ) -> dict[str, Any] | None:
        self.polls.append((ignore_subscribe_messages, timeout))
        step = self.script.pop(0) if self.script else None
        if isinstance(step, Exception):
            raise step
        return step

    async def aclose(self) -> None:
        self.closed = True


class FakeRedis:
    def __init__(self, pubsub: FakePubSub) -> None:
        self._pubsub = pubsub

    def pubsub(self) -> FakePubSub:
        return self._pubsub


def feed_of(pubsub: FakePubSub) -> RedisBoardFeed:
    return RedisBoardFeed(FakeRedis(pubsub), subscribe_timeout=0.5)  # type: ignore[arg-type]


async def test_signals_are_received_only_after_redis_confirmed_the_subscription() -> (
    None
):
    pubsub = FakePubSub(CONFIRMATION, SIGNAL)

    async with feed_of(pubsub).subscribe() as subscription:
        assert pubsub.subscribed == [CHANNEL]
        assert pubsub.polls == [(False, 0.5)]  # the confirmation is not skipped
        assert await subscription.wait(3.0) is True

    assert pubsub.closed


async def test_a_quiet_interval_is_not_a_signal() -> None:
    pubsub = FakePubSub(CONFIRMATION)

    async with feed_of(pubsub).subscribe() as subscription:
        assert await subscription.wait(3.0) is False

    assert pubsub.polls[-1] == (True, 3.0)


async def test_signals_that_piled_up_count_once() -> None:
    pubsub = FakePubSub(CONFIRMATION, SIGNAL, SIGNAL, SIGNAL)

    async with feed_of(pubsub).subscribe() as subscription:
        assert await subscription.wait(3.0) is True
        assert await subscription.wait(0.01) is False


async def test_an_unconfirmed_subscription_is_unavailable_and_released() -> None:
    pubsub = FakePubSub(None)

    with pytest.raises(BoardFeedUnavailable):
        async with feed_of(pubsub).subscribe():
            pytest.fail("nothing is received without a confirmed subscription")

    assert pubsub.closed


@pytest.mark.parametrize("error", [RedisConnectionError("down"), OSError("down")])
async def test_a_redis_that_fails_to_subscribe_is_unavailable(
    error: Exception,
) -> None:
    pubsub = FakePubSub()
    pubsub.subscribe_error = error

    with pytest.raises(BoardFeedUnavailable):
        async with feed_of(pubsub).subscribe():
            pytest.fail("not subscribed")

    assert pubsub.closed


@pytest.mark.parametrize(
    "error", [RedisConnectionError("down"), OSError("down"), TimeoutError()]
)
async def test_a_redis_that_fails_while_waiting_is_unavailable(
    error: Exception,
) -> None:
    pubsub = FakePubSub(CONFIRMATION, error)

    async with feed_of(pubsub).subscribe() as subscription:
        with pytest.raises(BoardFeedUnavailable):
            await subscription.wait(3.0)

    assert pubsub.closed


async def test_the_subscription_is_released_when_the_client_goes_away() -> None:
    pubsub = FakePubSub(CONFIRMATION)

    with pytest.raises(RuntimeError):
        async with feed_of(pubsub).subscribe():
            raise RuntimeError("client gone")

    assert pubsub.closed
