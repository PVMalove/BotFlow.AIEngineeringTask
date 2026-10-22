"""Broker adapters: stream entries to intents, and ack/reject/nack of events."""

from uuid import UUID, uuid4

import pytest
from kernel_platform.streams import StreamEntry

from application.commands.process_identity_event import InvalidEvent
from application.ports import QueuedIntent
from infrastructure.messaging import IdentityEventConsumer
from infrastructure.streams import RedisNotificationQueue


class FakeStream:
    def __init__(self) -> None:
        self.added: list[dict[str, str]] = []
        self.entries = [StreamEntry("1-0", {"intent_id": str(uuid4())})]
        self.acked: list[str] = []

    async def add(self, fields: dict[str, str]) -> str:
        self.added.append(fields)
        return "1-0"

    async def read(self, *, count: int, block_ms: int) -> list[StreamEntry]:
        return self.entries

    async def claim_idle(self, *, min_idle_ms: int, count: int) -> list[StreamEntry]:
        assert min_idle_ms == 30_000
        return self.entries

    async def ack(self, entry_id: str) -> None:
        self.acked.append(entry_id)


async def test_the_stream_carries_only_the_intent_id() -> None:
    stream, intent_id = FakeStream(), uuid4()

    await RedisNotificationQueue(stream).add(intent_id)  # type: ignore[arg-type]

    assert stream.added == [{"intent_id": str(intent_id)}]


async def test_entries_map_to_intents_and_junk_maps_to_none() -> None:
    stream = FakeStream()
    good = stream.entries[0]
    stream.entries = [
        good,
        StreamEntry("2-0", {}),
        StreamEntry("3-0", {"intent_id": "nope"}),
    ]
    sut = RedisNotificationQueue(stream)  # type: ignore[arg-type]

    assert await sut.read(5, 1) == await sut.claim_idle(5)
    assert await sut.read(5, 1) == [
        QueuedIntent("1-0", UUID(good.fields["intent_id"])),
        QueuedIntent("2-0", None),
        QueuedIntent("3-0", None),
    ]


class Message:
    message_id = "m1"
    type = "member.access-decided"
    body = b"{}"

    def __init__(self) -> None:
        self.outcome: tuple[str, bool | None] | None = None

    async def ack(self) -> None:
        self.outcome = ("ack", None)

    async def reject(self, requeue: bool) -> None:
        self.outcome = ("reject", requeue)

    async def nack(self, requeue: bool) -> None:
        self.outcome = ("nack", requeue)


class Process:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls: list[tuple[str | None, str | None, bytes]] = []

    async def execute(
        self, message_id: str | None, kind: str | None, body: bytes
    ) -> bool:
        self.calls.append((message_id, kind, body))
        if self.failure:
            raise self.failure
        return True


@pytest.mark.parametrize(
    ("failure", "outcome"),
    [
        (None, ("ack", None)),
        (InvalidEvent(), ("reject", False)),
        (ConnectionError("database down"), ("nack", True)),
    ],
)
async def test_a_message_is_acknowledged_only_after_it_is_stored(
    failure: Exception | None, outcome: tuple[str, bool | None]
) -> None:
    message, process = Message(), Process(failure)
    sut = IdentityEventConsumer(None, process, retry_delay=0)  # type: ignore[arg-type]

    await sut.handle(message)  # type: ignore[arg-type]

    assert process.calls == [("m1", "member.access-decided", b"{}")]
    assert message.outcome == outcome
