"""Redis Streams adapters: the notification queue and the voice job queue."""

import json
from uuid import UUID

from kernel_platform.streams import RedisStream, StreamEntry
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from application.ports import QueuedIntent, QueuedVoice

NOTIFICATION_STREAM = "botflow:automation:notifications"
NOTIFICATION_GROUP = "notifier"
STUCK_AFTER_MS = 30_000
VOICE_STREAM = "botflow:automation:voice"
VOICE_GROUP = "stt"
# Twice the slowest decode timeout and more (docs/whisper-validation.md): a record
# still pending after this long belongs to a worker that died.
VOICE_STUCK_AFTER_MS = 180_000


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


class RedisVoicePublisher:
    """Hands an outbox row of a voice job to the stream the STT worker reads."""

    def __init__(self, stream: RedisStream) -> None:
        self._stream = stream

    async def publish(
        self, routing_key: str, body: bytes, *, message_id: str, type: str
    ) -> None:
        job_id = json.loads(body)["payload"]["job_id"]
        try:
            await self._stream.add({"job_id": job_id, "outbox_id": message_id})
        except (RedisConnectionError, RedisTimeoutError, OSError):
            # The relay waits out an unavailable broker only for a ConnectionError
            # (an OSError); a Redis outage must not spend an attempt of the row.
            raise ConnectionError("Redis is unavailable.") from None


def _queued_voice(entry: StreamEntry) -> QueuedVoice:
    try:
        return QueuedVoice(entry.id, UUID(entry.fields["job_id"]))
    except (KeyError, ValueError):
        return QueuedVoice(entry.id, None)


class RedisVoiceQueue:
    def __init__(
        self, stream: RedisStream, stuck_after_ms: int = VOICE_STUCK_AFTER_MS
    ) -> None:
        self._stream = stream
        self._stuck_after_ms = stuck_after_ms

    async def ensure_group(self) -> None:
        await self._stream.ensure_group()

    async def claim_idle(self, count: int) -> list[QueuedVoice]:
        entries = await self._stream.claim_idle(
            min_idle_ms=self._stuck_after_ms, count=count
        )
        return [_queued_voice(entry) for entry in entries]

    async def read(self, count: int, block_ms: int) -> list[QueuedVoice]:
        entries = await self._stream.read(count=count, block_ms=block_ms)
        return [_queued_voice(entry) for entry in entries]

    async def ack(self, entry_id: str) -> None:
        await self._stream.ack(entry_id)
