"""Outbox -> Redis Stream hand-off of voice jobs, against a scripted Redis client."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from kernel_platform.outbox import OutboxRow, RelayOutbox
from kernel_platform.streams import RedisStream
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

from api.containers import VoiceFlow, build_voice_flow
from application.ports import QueuedVoice
from infrastructure.streams import (
    VOICE_GROUP,
    VOICE_STREAM,
    VOICE_STUCK_AFTER_MS,
    RedisVoicePublisher,
    RedisVoiceQueue,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.failure: Exception | None = None
        self.read_result: Any = []
        self.claim_result: Any = ["0-0", [], []]

    async def xadd(self, name: str, fields: dict[str, str]) -> bytes:
        if self.failure:
            raise self.failure
        self.calls.append(("xadd", (name, fields)))
        return b"1-0"

    async def xgroup_create(self, name: str, groupname: str, **kwargs: Any) -> None:
        self.calls.append(("xgroup_create", (name, groupname, kwargs)))

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
    return RedisStream(redis, VOICE_STREAM, VOICE_GROUP, "stt-1")  # type: ignore[arg-type]


def body(job_id: UUID) -> bytes:
    return json.dumps({"payload": {"job_id": str(job_id)}}).encode()


async def test_the_publisher_adds_the_job_and_outbox_ids_to_the_voice_stream() -> None:
    redis, job_id, outbox_id = FakeRedis(), uuid4(), uuid4()

    await RedisVoicePublisher(stream(redis)).publish(
        "voice.transcribe", body(job_id), message_id=str(outbox_id), type="x"
    )

    assert redis.calls == [
        (
            "xadd",
            (
                "botflow:automation:voice",
                {"job_id": str(job_id), "outbox_id": str(outbox_id)},
            ),
        )
    ]


@pytest.mark.parametrize(
    "failure", [RedisConnectionError("x"), RedisTimeoutError("x"), TimeoutError()]
)
async def test_a_redis_outage_surfaces_as_a_connection_error(
    failure: Exception,
) -> None:
    redis = FakeRedis()
    redis.failure = failure

    with pytest.raises(ConnectionError):
        await RedisVoicePublisher(stream(redis)).publish(
            "voice.transcribe", body(uuid4()), message_id=str(uuid4()), type="x"
        )


async def test_a_command_error_is_not_mistaken_for_an_outage() -> None:
    redis = FakeRedis()
    redis.failure = ResponseError("NOPERM")

    with pytest.raises(ResponseError):
        await RedisVoicePublisher(stream(redis)).publish(
            "voice.transcribe", body(uuid4()), message_id=str(uuid4()), type="x"
        )


class Tx:
    def __init__(self, rows: list[OutboxRow]) -> None:
        self.rows = rows
        self.published: list[UUID] = []
        self.rescheduled: list[tuple[UUID, int, datetime | None]] = []

    async def claim_due(self, now: datetime, limit: int) -> list[OutboxRow]:
        return self.rows

    async def mark_published(self, entry_id: UUID, now: datetime) -> None:
        self.published.append(entry_id)

    async def reschedule(
        self, entry_id: UUID, attempts: int, next_attempt_at: Any, error: str
    ) -> None:
        self.rescheduled.append((entry_id, attempts, next_attempt_at))


def relay_for(redis: FakeRedis, tx: Tx) -> RelayOutbox[Tx]:
    @asynccontextmanager
    async def transaction() -> AsyncIterator[Tx]:
        yield tx

    return RelayOutbox(
        transaction, RedisVoicePublisher(stream(redis)), clock=lambda: NOW
    )


def voice_row(attempts: int = 0) -> OutboxRow:
    job_id = uuid4()
    return OutboxRow(
        id=uuid4(),
        type="TranscribeVoice",
        routing_key="voice.transcribe",
        payload={"payload": {"job_id": str(job_id)}},
        created_at=NOW,
        attempts=attempts,
        next_attempt_at=NOW,
        correlation_id=job_id,
    )


async def test_the_relay_marks_a_row_published_after_the_stream_add() -> None:
    redis, row = FakeRedis(), voice_row()
    tx = Tx([row])

    assert await relay_for(redis, tx).execute() == 1

    assert tx.published == [row.id] and len(redis.calls) == 1


async def test_a_redis_outage_spends_no_attempt_of_the_row() -> None:
    redis, row = FakeRedis(), voice_row(attempts=1)
    redis.failure = RedisConnectionError("down")
    tx = Tx([row])

    assert await relay_for(redis, tx).execute() == 0

    ((entry_id, attempts, due),) = tx.rescheduled
    assert (entry_id, attempts) == (row.id, 1) and due is not None
    assert tx.published == []


async def test_the_queue_reads_one_entry_at_a_time_for_the_stt_group() -> None:
    redis, job_id = FakeRedis(), uuid4()
    redis.read_result = [
        [VOICE_STREAM, [("5-0", {"job_id": str(job_id), "outbox_id": "o"})]]
    ]
    queue = RedisVoiceQueue(stream(redis))

    entries = await queue.read(1, 5000)

    assert entries == [QueuedVoice("5-0", job_id)]
    ((name, (group, consumer, kwargs)),) = redis.calls
    assert (name, group, consumer) == ("xreadgroup", "stt", "stt-1")
    assert kwargs["count"] == 1 and kwargs["block"] == 5000


async def test_a_stuck_entry_is_taken_over_after_one_hundred_eighty_seconds() -> None:
    redis, job_id = FakeRedis(), uuid4()
    redis.claim_result = ["0-0", [("6-0", {"job_id": str(job_id)})], []]
    queue = RedisVoiceQueue(stream(redis))

    entries = await queue.claim_idle(1)

    assert VOICE_STUCK_AFTER_MS == 180_000
    assert entries == [QueuedVoice("6-0", job_id)]
    ((_, (_, _, _, kwargs)),) = redis.calls
    assert kwargs["min_idle_time"] == 180_000 and kwargs["count"] == 1


async def test_an_entry_without_a_valid_job_id_is_returned_without_one() -> None:
    redis = FakeRedis()
    redis.read_result = [[VOICE_STREAM, [("7-0", {}), ("8-0", {"job_id": "nope"})]]]
    queue = RedisVoiceQueue(stream(redis))

    assert await queue.read(2, 1) == [
        QueuedVoice("7-0", None),
        QueuedVoice("8-0", None),
    ]


async def test_ack_and_group_creation_use_the_stt_group() -> None:
    redis = FakeRedis()
    queue = RedisVoiceQueue(stream(redis))

    await queue.ensure_group()
    await queue.ack("9-0")

    assert redis.calls == [
        ("xgroup_create", (VOICE_STREAM, "stt", {"id": "0", "mkstream": True})),
        ("xack", (VOICE_STREAM, "stt", ("9-0",))),
    ]


def test_the_voice_flow_relays_the_outbox_and_reads_the_same_stream() -> None:
    resources = SimpleNamespace(redis=FakeRedis(), sessions=object())

    flow = build_voice_flow(resources, consumer_name="w-1")  # type: ignore[arg-type]

    assert isinstance(flow, VoiceFlow)
    assert isinstance(flow.relay, RelayOutbox)
    assert isinstance(flow.queue, RedisVoiceQueue)


def test_the_voice_flow_needs_the_redis_connection() -> None:
    resources = SimpleNamespace(redis=None, sessions=object())

    with pytest.raises(RuntimeError):
        build_voice_flow(resources, consumer_name="w-1")  # type: ignore[arg-type]
