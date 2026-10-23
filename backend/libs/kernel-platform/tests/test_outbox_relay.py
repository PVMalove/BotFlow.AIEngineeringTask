"""RelayOutbox against an in-memory transaction: publish, retry, exhaust, replay."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from kernel_platform.outbox import OutboxRow, RelayOutbox
from kernel_platform.retry import RetryPolicy

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class Tx:
    """Stores rows the way the SQL transaction does; commit is explicit."""

    def __init__(self, rows: dict[UUID, OutboxRow]) -> None:
        self.rows = rows
        self.published_at: dict[UUID, datetime] = {}
        self.errors: dict[UUID, str] = {}
        self.exhausted_calls: list[UUID] = []

    async def claim_due(self, now: datetime, limit: int) -> list[OutboxRow]:
        due = [
            r
            for r in self.rows.values()
            if r.id not in self.published_at
            and r.next_attempt_at is not None
            and r.next_attempt_at <= now
        ]
        return sorted(due, key=lambda r: r.created_at)[:limit]

    async def mark_published(self, entry_id: UUID, now: datetime) -> None:
        self.published_at[entry_id] = now

    async def reschedule(
        self,
        entry_id: UUID,
        attempts: int,
        next_attempt_at: datetime | None,
        error: str,
    ) -> None:
        row = self.rows[entry_id]
        self.rows[entry_id] = OutboxRow(
            id=row.id,
            type=row.type,
            routing_key=row.routing_key,
            payload=row.payload,
            created_at=row.created_at,
            attempts=attempts,
            next_attempt_at=next_attempt_at,
        )
        self.errors[entry_id] = error


class Store:
    def __init__(self) -> None:
        self.rows: dict[UUID, OutboxRow] = {}
        self.commits = 0
        self.rollbacks = 0
        self.last_tx: Tx | None = None

    def add(self, **overrides: object) -> OutboxRow:
        values: dict[str, object] = dict(
            id=uuid4(),
            type="CreateTask",
            routing_key="task.create",
            payload={"message_id": "m"},
            created_at=NOW,
            attempts=0,
            next_attempt_at=NOW,
        )
        values.update(overrides)
        row = OutboxRow(**values)  # type: ignore[arg-type]
        self.rows[row.id] = row
        return row

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Tx]:
        tx = Tx(self.rows)
        self.last_tx = tx
        try:
            yield tx
        except BaseException:
            self.rollbacks += 1
            raise
        self.commits += 1


class Publisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, bytes, str, str]] = []
        self.failure: BaseException | None = None

    async def publish(
        self, routing_key: str, body: bytes, *, message_id: str, type: str
    ) -> None:
        if self.failure:
            raise self.failure
        self.published.append((routing_key, body, message_id, type))


def relay(
    store: Store,
    publisher: Publisher,
    now: datetime = NOW,
    on_exhausted: object = None,
) -> RelayOutbox[Tx]:
    return RelayOutbox(
        store.transaction,
        publisher,
        clock=lambda: now,
        policy=RetryPolicy(jitter=0.0),
        on_exhausted=on_exhausted,  # type: ignore[arg-type]
    )


async def test_a_due_row_is_published_with_its_identity_and_marked_published() -> None:
    store, publisher = Store(), Publisher()
    row = store.add(payload={"a": 1})

    published = await relay(store, publisher).execute()

    assert published == 1
    assert publisher.published == [
        ("task.create", b'{"a":1}', str(row.id), "CreateTask")
    ]
    assert store.last_tx is not None and row.id in store.last_tx.published_at
    assert store.commits == 1


async def test_a_row_that_is_not_due_yet_is_left_alone() -> None:
    store, publisher = Store(), Publisher()
    store.add(next_attempt_at=NOW + timedelta(seconds=5))

    assert await relay(store, publisher).execute() == 0
    assert publisher.published == []


async def test_a_failed_publish_is_rescheduled_five_seconds_later_with_the_error() -> (
    None
):
    store, publisher = Store(), Publisher()
    row = store.add()
    publisher.failure = RuntimeError("nack with password=secret")

    await relay(store, publisher).execute()

    stored = store.rows[row.id]
    assert stored.attempts == 1
    assert stored.next_attempt_at == NOW + timedelta(seconds=5)
    assert store.last_tx is not None
    error = store.last_tx.errors[row.id]
    assert "RuntimeError" in error and "secret" not in error  # no driver text stored


async def test_the_second_failure_waits_thirty_seconds() -> None:
    store, publisher = Store(), Publisher()
    row = store.add(attempts=1)
    publisher.failure = RuntimeError("nack")

    await relay(store, publisher).execute()

    assert store.rows[row.id].attempts == 2
    assert store.rows[row.id].next_attempt_at == NOW + timedelta(seconds=30)


async def test_the_third_failure_exhausts_the_row_and_calls_the_hook_in_the_tx() -> (
    None
):
    store, publisher = Store(), Publisher()
    row = store.add(attempts=2)
    publisher.failure = RuntimeError("nack")
    hooked: list[tuple[Tx, UUID, str]] = []

    async def on_exhausted(tx: Tx, entry: OutboxRow, error: str) -> None:
        hooked.append((tx, entry.id, error))

    await relay(store, publisher, on_exhausted=on_exhausted).execute()

    assert store.rows[row.id].attempts == 3
    assert store.rows[row.id].next_attempt_at is None
    assert [(h[1]) for h in hooked] == [row.id]
    assert hooked[0][0] is store.last_tx  # same transaction as the exhaustion
    assert "RuntimeError" in hooked[0][2]
    assert store.commits == 1


async def test_an_unavailable_broker_does_not_spend_an_attempt() -> None:
    store, publisher = Store(), Publisher()
    row = store.add(attempts=1)
    publisher.failure = ConnectionRefusedError("broker down")

    await relay(store, publisher).execute()

    stored = store.rows[row.id]
    assert stored.attempts == 1  # ADR-0007: waiting for infrastructure is free
    assert stored.next_attempt_at == NOW + timedelta(seconds=5)
    assert store.last_tx is not None
    assert "ConnectionRefusedError" in store.last_tx.errors[row.id]


async def test_attempts_and_delays_survive_a_restart_and_stop_at_three() -> None:
    store, publisher = Store(), Publisher()
    row = store.add()
    publisher.failure = RuntimeError("nack")
    exhausted: list[UUID] = []

    async def on_exhausted(tx: Tx, entry: OutboxRow, error: str) -> None:
        exhausted.append(entry.id)

    # Every step is a new relay over the stored row, as after a process restart.
    await relay(store, publisher, NOW, on_exhausted).execute()
    await relay(store, publisher, NOW + timedelta(seconds=4), on_exhausted).execute()
    assert store.rows[row.id].attempts == 1
    await relay(store, publisher, NOW + timedelta(seconds=5), on_exhausted).execute()
    assert store.rows[row.id].attempts == 2
    assert store.rows[row.id].next_attempt_at == NOW + timedelta(seconds=35)
    await relay(store, publisher, NOW + timedelta(seconds=34), on_exhausted).execute()
    assert store.rows[row.id].attempts == 2 and exhausted == []
    await relay(store, publisher, NOW + timedelta(seconds=35), on_exhausted).execute()

    assert store.rows[row.id].attempts == 3
    assert store.rows[row.id].next_attempt_at is None
    assert exhausted == [row.id]
    await relay(store, publisher, NOW + timedelta(hours=1), on_exhausted).execute()
    assert store.rows[row.id].attempts == 3 and exhausted == [row.id]  # no 4th try


async def test_a_long_outage_never_exhausts_the_row_or_requeues_it_at_once() -> None:
    store, publisher = Store(), Publisher()
    row = store.add()
    publisher.failure = ConnectionRefusedError("broker down")
    exhausted: list[UUID] = []

    async def on_exhausted(tx: Tx, entry: OutboxRow, error: str) -> None:
        exhausted.append(entry.id)

    now = NOW
    for _ in range(10):
        await relay(store, publisher, now, on_exhausted).execute()
        due = store.rows[row.id].next_attempt_at
        assert due is not None and due >= now + timedelta(seconds=5)
        now = due

    assert (store.rows[row.id].attempts, exhausted) == (0, [])


async def test_one_failing_row_does_not_stop_the_rest_of_the_batch() -> None:
    store = Store()
    first = store.add(created_at=NOW)
    second = store.add(created_at=NOW + timedelta(milliseconds=1))

    class Flaky(Publisher):
        async def publish(
            self, routing_key: str, body: bytes, *, message_id: str, type: str
        ) -> None:
            if message_id == str(first.id):
                raise RuntimeError("nack")
            await super().publish(routing_key, body, message_id=message_id, type=type)

    flaky = Flaky()
    assert await relay(store, flaky).execute() == 1
    assert [p[2] for p in flaky.published] == [str(second.id)]


async def test_a_storage_error_propagates_and_rolls_back() -> None:
    store, publisher = Store(), Publisher()
    store.add()

    class Broken(Tx):
        async def mark_published(self, entry_id: UUID, now: datetime) -> None:
            raise OSError("pg down")

    @asynccontextmanager
    async def transaction() -> AsyncIterator[Tx]:
        async with store.transaction() as tx:
            broken = Broken(tx.rows)
            yield broken

    sut = RelayOutbox(transaction, publisher, clock=lambda: NOW)
    with pytest.raises(OSError):
        await sut.execute()
    assert store.rollbacks == 1 and store.commits == 0
