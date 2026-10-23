"""Redis Streams consumer-group helper against a scripted client."""

from typing import Any

import pytest
from redis.exceptions import ResponseError

from kernel_platform.streams import RedisStream, StreamEntry


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.group_error: Exception | None = None
        self.read_result: Any = []
        self.claim_result: Any = ["0-0", [], []]

    async def xgroup_create(self, name: str, groupname: str, **kwargs: Any) -> None:
        self.calls.append(("xgroup_create", (name, groupname, kwargs)))
        if self.group_error:
            raise self.group_error

    async def xadd(self, name: str, fields: dict[str, str]) -> bytes:
        self.calls.append(("xadd", (name, fields)))
        return b"1-0"

    async def xreadgroup(self, groupname: str, consumername: str, **kwargs: Any) -> Any:
        self.calls.append(("xreadgroup", (groupname, consumername, kwargs)))
        return self.read_result

    async def xautoclaim(
        self, name: str, groupname: str, consumername: str, **kw: Any
    ) -> Any:
        self.calls.append(("xautoclaim", (name, groupname, consumername, kw)))
        return self.claim_result

    async def xack(self, name: str, groupname: str, *ids: str) -> int:
        self.calls.append(("xack", (name, groupname, ids)))
        return 1


def stream(redis: FakeRedis) -> RedisStream:
    return RedisStream(redis, "botflow:test", "notifier", "worker-1")  # type: ignore[arg-type]


async def test_group_is_created_with_the_stream_and_an_existing_one_is_fine() -> None:
    redis = FakeRedis()
    redis.group_error = ResponseError("BUSYGROUP Consumer Group name already exists")

    await stream(redis).ensure_group()

    assert redis.calls == [
        ("xgroup_create", ("botflow:test", "notifier", {"id": "0", "mkstream": True}))
    ]


async def test_other_group_errors_are_not_hidden() -> None:
    redis = FakeRedis()
    redis.group_error = ResponseError("NOPERM this user has no permissions")

    with pytest.raises(ResponseError):
        await stream(redis).ensure_group()


async def test_add_returns_the_entry_id_as_text() -> None:
    redis = FakeRedis()

    assert await stream(redis).add({"intent_id": "abc"}) == "1-0"
    assert redis.calls == [("xadd", ("botflow:test", {"intent_id": "abc"}))]


async def test_read_decodes_new_entries_for_this_consumer() -> None:
    redis = FakeRedis()
    redis.read_result = [[b"botflow:test", [(b"1-0", {b"intent_id": b"abc"})]]]

    entries = await stream(redis).read(count=5, block_ms=100)

    assert entries == [StreamEntry("1-0", {"intent_id": "abc"})]
    assert redis.calls == [
        (
            "xreadgroup",
            (
                "notifier",
                "worker-1",
                {"streams": {"botflow:test": ">"}, "count": 5, "block": 100},
            ),
        )
    ]


async def test_read_with_nothing_new_is_empty() -> None:
    redis = FakeRedis()
    redis.read_result = None

    assert await stream(redis).read(count=5, block_ms=1) == []


async def test_claim_idle_takes_over_stuck_entries() -> None:
    redis = FakeRedis()
    redis.claim_result = [b"0-0", [(b"2-0", {b"intent_id": b"def"})], []]

    entries = await stream(redis).claim_idle(min_idle_ms=30_000, count=10)

    assert entries == [StreamEntry("2-0", {"intent_id": "def"})]


async def test_ack_confirms_the_entry_to_the_group() -> None:
    redis = FakeRedis()

    await stream(redis).ack("2-0")

    assert redis.calls == [("xack", ("botflow:test", "notifier", ("2-0",)))]
