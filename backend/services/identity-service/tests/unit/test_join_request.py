"""Join submission against in-memory ports: the member row is the request."""

from dataclasses import replace
from uuid import uuid4

import pytest
from conftest import Clock, FakeRateLimiter, FakeUnitOfWork, Store

from application.commands.join import SubmitJoinRequest, SubmitJoinRequestCommand
from domain.access import JoinOutcome
from domain.entities.auth import Member, MemberStatus, RateLimited, Role

APPLICANT = 7_000_000_001


@pytest.fixture
def store() -> Store:
    return Store()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def limiter() -> FakeRateLimiter:
    return FakeRateLimiter()


@pytest.fixture
def sut(store: Store, clock: Clock, limiter: FakeRateLimiter) -> SubmitJoinRequest:
    return SubmitJoinRequest(lambda: FakeUnitOfWork(store), limiter, clock)


def only_member(store: Store) -> Member:
    assert len(store.members) == 1
    return next(iter(store.members.values()))


def existing(store: Store, clock: Clock, status: MemberStatus) -> Member:
    member = Member(
        id=uuid4(),
        telegram_id=APPLICANT,
        role=Role.USER,
        status=status,
        created_at=clock(),
        access_version=3,
    )
    store.members[member.id] = member
    return member


async def test_a_new_person_becomes_a_pending_member(
    sut: SubmitJoinRequest, store: Store
) -> None:
    result = await sut.execute(SubmitJoinRequestCommand(APPLICANT, "Ann @ann"))

    assert result.outcome is JoinOutcome.SUBMITTED
    member = only_member(store)
    assert (member.telegram_id, member.role, member.status) == (
        APPLICANT,
        Role.USER,
        MemberStatus.PENDING,
    )
    assert member.display_name == "Ann @ann"
    assert member.access_version == 1
    assert result.member == member


async def test_a_second_request_while_pending_changes_nothing(
    sut: SubmitJoinRequest, store: Store
) -> None:
    await sut.execute(SubmitJoinRequestCommand(APPLICANT))
    before = only_member(store)

    result = await sut.execute(SubmitJoinRequestCommand(APPLICANT, "Other name"))

    assert result.outcome is JoinOutcome.ALREADY_PENDING
    assert only_member(store) == before


async def test_an_approved_member_is_told_they_already_belong(
    sut: SubmitJoinRequest, store: Store, clock: Clock
) -> None:
    approved = existing(store, clock, MemberStatus.APPROVED)

    result = await sut.execute(SubmitJoinRequestCommand(APPLICANT))

    assert result.outcome is JoinOutcome.ALREADY_MEMBER
    assert only_member(store) == approved


async def test_a_rejected_applicant_can_apply_again_with_a_new_version(
    sut: SubmitJoinRequest, store: Store, clock: Clock
) -> None:
    rejected = existing(store, clock, MemberStatus.REJECTED)
    store.members[rejected.id] = replace(rejected, decided_by=rejected.id)

    result = await sut.execute(SubmitJoinRequestCommand(APPLICANT, "Ann"))

    assert result.outcome is JoinOutcome.SUBMITTED
    member = only_member(store)
    assert member.id == rejected.id
    assert member.status is MemberStatus.PENDING
    assert member.access_version == rejected.access_version + 1
    assert member.status_changed_at == clock()
    assert member.decided_by is None
    assert member.display_name == "Ann"


async def test_a_blocked_member_cannot_bypass_the_block(
    sut: SubmitJoinRequest, store: Store, clock: Clock
) -> None:
    blocked = existing(store, clock, MemberStatus.BLOCKED)

    result = await sut.execute(SubmitJoinRequestCommand(APPLICANT, "New name"))

    assert result.outcome is JoinOutcome.BLOCKED
    assert only_member(store) == blocked


async def test_the_sixth_request_in_a_minute_is_rate_limited(
    sut: SubmitJoinRequest, store: Store
) -> None:
    for _ in range(5):
        await sut.execute(SubmitJoinRequestCommand(APPLICANT))

    with pytest.raises(RateLimited):
        await sut.execute(SubmitJoinRequestCommand(APPLICANT))


async def test_the_limit_is_kept_per_telegram_id(
    sut: SubmitJoinRequest, limiter: FakeRateLimiter
) -> None:
    for _ in range(5):
        await sut.execute(SubmitJoinRequestCommand(APPLICANT))

    result = await sut.execute(SubmitJoinRequestCommand(APPLICANT + 1))

    assert result.outcome is JoinOutcome.SUBMITTED
    assert limiter.hits[f"join:{APPLICANT}"] == 5
