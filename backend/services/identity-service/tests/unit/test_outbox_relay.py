"""Outbox intents written by decisions and relayed with bounded retries."""

from datetime import timedelta
from uuid import uuid4

import pytest
from conftest import NOW, Clock, FakeUnitOfWork, Store

from application.commands.access import DecideMember, DecideMemberCommand
from application.commands.relay import RelayOutbox
from domain.access import ACCESS_DECIDED, Action, OutboxEntry, VersionConflict
from domain.entities.auth import Member, MemberStatus, Role


def member(
    status: MemberStatus, role: Role = Role.USER, telegram_id: int = 1
) -> Member:
    return Member(uuid4(), telegram_id, role, status, NOW)


class World:
    def __init__(self) -> None:
        self.store = Store()
        self.clock = Clock()
        self.admin = member(MemberStatus.APPROVED, Role.ADMIN, 1)
        self.second = member(MemberStatus.APPROVED, Role.ADMIN, 2)
        self.applicant = member(MemberStatus.PENDING, telegram_id=3)
        for m in (self.admin, self.second, self.applicant):
            self.store.members[m.id] = m
        self.decide = DecideMember(lambda: FakeUnitOfWork(self.store), self.clock)

    async def act(self, action: Action, target: Member, version: int | None = None):  # type: ignore[no-untyped-def]
        current = self.store.members[target.id]
        return await self.decide.execute(
            DecideMemberCommand(
                self.admin.id,
                target.id,
                action,
                current.access_version if version is None else version,
            )
        )


@pytest.fixture
def world() -> World:
    return World()


@pytest.mark.parametrize(
    ("action", "decision"),
    [(Action.APPROVE, "approved"), (Action.REJECT, "rejected")],
)
async def test_a_decision_stores_exactly_one_intent_in_its_transaction(
    world: World, action: Action, decision: str
) -> None:
    await world.act(action, world.applicant)

    (entry,) = world.store.outbox.values()
    assert entry.type == ACCESS_DECIDED
    assert entry.payload == {
        "member_id": str(world.applicant.id),
        "telegram_id": 3,
        "decision": decision,
        "access_version": 2,
        "decided_at": NOW.isoformat(),
    }
    assert (entry.attempts, entry.published_at) == (0, None)
    assert entry.next_attempt_at == NOW


async def test_unblock_notifies_with_the_new_access_version(world: World) -> None:
    await world.act(Action.BLOCK, world.second)
    world.store.outbox.clear()

    await world.act(Action.UNBLOCK, world.second)

    (entry,) = world.store.outbox.values()
    assert entry.payload["decision"] == "unblocked"
    assert entry.payload["access_version"] == 3


async def test_a_block_leaves_no_intent_for_the_blocked_author(world: World) -> None:
    await world.act(Action.BLOCK, world.second)

    assert world.store.outbox == {}


async def test_a_repeat_adds_no_second_intent(world: World) -> None:
    await world.act(Action.APPROVE, world.applicant)

    again = await world.act(Action.APPROVE, world.applicant, version=1)

    assert not again.changed
    assert len(world.store.outbox) == 1


async def test_a_refused_decision_leaves_no_intent(world: World) -> None:
    with pytest.raises(VersionConflict):
        await world.act(Action.APPROVE, world.applicant, version=7)

    assert world.store.outbox == {}


# ---- relay -----------------------------------------------------------------


class Broker:
    def __init__(self) -> None:
        self.published: list[OutboxEntry] = []
        self.failures = 0

    async def publish(self, entry: OutboxEntry) -> None:
        if self.failures:
            self.failures -= 1
            raise ConnectionError("broker down")
        self.published.append(entry)


def relay(world: World, broker: Broker) -> RelayOutbox:
    return RelayOutbox(lambda: FakeUnitOfWork(world.store), broker, world.clock)


async def test_confirmed_entries_are_marked_published_once(world: World) -> None:
    broker = Broker()
    await world.act(Action.APPROVE, world.applicant)

    first = await relay(world, broker).execute()
    second = await relay(world, broker).execute()

    assert (first, second) == (1, 0)
    assert len(broker.published) == 1
    (entry,) = world.store.outbox.values()
    assert entry.published_at == NOW


async def test_failures_are_retried_after_5_and_30_seconds_then_stop(
    world: World,
) -> None:
    broker = Broker()
    broker.failures = 99
    await world.act(Action.APPROVE, world.applicant)
    sut = relay(world, broker)

    assert await sut.execute() == 0
    (entry,) = world.store.outbox.values()
    assert (entry.attempts, entry.next_attempt_at) == (1, NOW + timedelta(seconds=5))

    world.clock.advance(seconds=4)
    assert await sut.execute() == 0  # not due yet
    assert next(iter(world.store.outbox.values())).attempts == 1

    world.clock.advance(seconds=1)
    await sut.execute()
    (entry,) = world.store.outbox.values()
    assert (entry.attempts, entry.next_attempt_at) == (
        2,
        NOW + timedelta(seconds=35),
    )

    world.clock.advance(seconds=30)
    await sut.execute()
    (entry,) = world.store.outbox.values()
    assert (entry.attempts, entry.next_attempt_at, entry.published_at) == (
        3,
        None,
        None,
    )
    world.clock.advance(hours=1)
    assert await sut.execute() == 0


async def test_a_recovered_broker_receives_the_entry_on_the_retry(
    world: World,
) -> None:
    broker = Broker()
    broker.failures = 1
    await world.act(Action.REJECT, world.applicant)
    sut = relay(world, broker)
    await sut.execute()
    world.clock.advance(seconds=5)

    assert await sut.execute() == 1

    (entry,) = world.store.outbox.values()
    assert entry.published_at is not None
    assert broker.published[0].payload["decision"] == "rejected"
