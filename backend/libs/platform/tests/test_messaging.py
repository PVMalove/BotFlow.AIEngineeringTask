"""Confirmed, mandatory, persistent publishing and the shared topology."""

from typing import Any

import pytest
from aio_pika import DeliveryMode, ExchangeType, Message

from botflow_platform.messaging import (
    ACCESS_DECIDED_ROUTING_KEY,
    AUTOMATION_IDENTITY_QUEUE,
    IDENTITY_EVENTS_EXCHANGE,
    ConfirmedPublisher,
)


class FakeExchange:
    def __init__(self, log: list[Any]) -> None:
        self.log = log
        self.failure: Exception | None = None

    async def publish(
        self, message: Message, routing_key: str, *, mandatory: bool, timeout: float
    ) -> None:
        if self.failure:
            raise self.failure
        self.log.append(("publish", message, routing_key, mandatory))


class FakeQueue:
    def __init__(self, log: list[Any]) -> None:
        self.log = log

    async def bind(self, exchange: Any, routing_key: str) -> None:
        self.log.append(("bind", routing_key))


class FakeChannel:
    def __init__(self, log: list[Any]) -> None:
        self.log = log
        self.exchange = FakeExchange(log)

    async def declare_exchange(self, name: str, kind: Any, *, durable: bool) -> Any:
        self.log.append(("exchange", name, kind, durable))
        return self.exchange

    async def declare_queue(self, name: str, *, durable: bool) -> FakeQueue:
        self.log.append(("queue", name, durable))
        return FakeQueue(self.log)


class FakeConnection:
    def __init__(self) -> None:
        self.log: list[Any] = []
        self.channels: list[FakeChannel] = []

    async def channel(self, *, publisher_confirms: bool) -> FakeChannel:
        assert publisher_confirms
        self.channels.append(FakeChannel(self.log))
        return self.channels[-1]


async def test_declares_durable_topology_before_the_first_publish() -> None:
    connection = FakeConnection()
    sut = ConfirmedPublisher(connection)  # type: ignore[arg-type]

    await sut.publish(ACCESS_DECIDED_ROUTING_KEY, b"{}", message_id="e1", type="t")

    assert connection.log[:3] == [
        ("exchange", IDENTITY_EVENTS_EXCHANGE, ExchangeType.TOPIC, True),
        ("queue", AUTOMATION_IDENTITY_QUEUE, True),
        ("bind", "member.*"),
    ]


async def test_messages_are_persistent_mandatory_and_identified() -> None:
    connection = FakeConnection()
    sut = ConfirmedPublisher(connection)  # type: ignore[arg-type]

    await sut.publish(ACCESS_DECIDED_ROUTING_KEY, b'{"a":1}', message_id="e1", type="t")

    *_, (kind, message, routing_key, mandatory) = connection.log
    assert kind == "publish"
    assert message.body == b'{"a":1}'
    assert message.delivery_mode is DeliveryMode.PERSISTENT
    assert (message.message_id, message.type) == ("e1", "t")
    assert message.content_type == "application/json"
    assert (routing_key, mandatory) == (ACCESS_DECIDED_ROUTING_KEY, True)


async def test_a_failed_publish_raises_and_reopens_the_channel_next_time() -> None:
    connection = FakeConnection()
    sut = ConfirmedPublisher(connection)  # type: ignore[arg-type]
    await sut.publish("member.x", b"{}", message_id="e1", type="t")
    connection.channels[0].exchange.failure = RuntimeError("unroutable")

    with pytest.raises(RuntimeError):
        await sut.publish("member.x", b"{}", message_id="e2", type="t")
    await sut.publish("member.x", b"{}", message_id="e3", type="t")

    assert len(connection.channels) == 2
