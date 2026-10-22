"""Redis Streams consumer group: add, read new, take over stuck, acknowledge."""

from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError


@dataclass(frozen=True, slots=True)
class StreamEntry:
    id: str
    fields: dict[str, str]


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


def _entries(raw: Any) -> list[StreamEntry]:
    return [
        StreamEntry(_text(entry_id), {_text(k): _text(v) for k, v in fields.items()})
        for entry_id, fields in raw
    ]


class RedisStream:
    """One stream and one consumer group; `consumer` names this process."""

    def __init__(self, redis: Redis, key: str, group: str, consumer: str) -> None:
        self._redis = redis
        self._key = key
        self._group = group
        self._consumer = consumer

    async def ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(
                self._key, self._group, id="0", mkstream=True
            )
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise

    async def add(self, fields: dict[str, str]) -> str:
        return _text(await self._redis.xadd(self._key, fields))  # type: ignore[arg-type]

    async def read(self, *, count: int, block_ms: int) -> list[StreamEntry]:
        """Entries never delivered to the group; empty after `block_ms` of silence."""
        raw: Any = await self._redis.xreadgroup(
            self._group,
            self._consumer,
            streams={self._key: ">"},
            count=count,
            block=block_ms,
        )
        return _entries(raw[0][1]) if raw else []

    async def claim_idle(self, *, min_idle_ms: int, count: int) -> list[StreamEntry]:
        """Entries delivered earlier but not acknowledged for `min_idle_ms`."""
        raw: Any = await self._redis.xautoclaim(
            self._key,
            self._group,
            self._consumer,
            min_idle_time=min_idle_ms,
            start_id="0-0",
            count=count,
        )
        return _entries(raw[1])

    async def ack(self, entry_id: str) -> None:
        await self._redis.xack(self._key, self._group, entry_id)
