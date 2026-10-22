"""Redis Streams adapter of the notification queue."""

from uuid import UUID

from botflow_platform.streams import RedisStream, StreamEntry

from application.ports import QueuedIntent

NOTIFICATION_STREAM = "botflow:automation:notifications"
NOTIFICATION_GROUP = "notifier"
STUCK_AFTER_MS = 30_000


def _queued(entry: StreamEntry) -> QueuedIntent:
    try:
        return QueuedIntent(entry.id, UUID(entry.fields["intent_id"]))
    except (KeyError, ValueError):
        return QueuedIntent(entry.id, None)


class RedisNotificationQueue:
    def __init__(
        self, stream: RedisStream, stuck_after_ms: int = STUCK_AFTER_MS
    ) -> None:
        self._stream = stream
        self._stuck_after_ms = stuck_after_ms

    async def ensure_group(self) -> None:
        await self._stream.ensure_group()

    async def add(self, intent_id: UUID) -> None:
        await self._stream.add({"intent_id": str(intent_id)})

    async def claim_idle(self, count: int) -> list[QueuedIntent]:
        entries = await self._stream.claim_idle(
            min_idle_ms=self._stuck_after_ms, count=count
        )
        return [_queued(entry) for entry in entries]

    async def read(self, count: int, block_ms: int) -> list[QueuedIntent]:
        entries = await self._stream.read(count=count, block_ms=block_ms)
        return [_queued(entry) for entry in entries]

    async def ack(self, entry_id: str) -> None:
        await self._stream.ack(entry_id)
