"""Stream adapter: notification stream entries to intents."""

from uuid import UUID, uuid4

from kernel_platform.streams import StreamEntry

from application.ports import QueuedIntent
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
