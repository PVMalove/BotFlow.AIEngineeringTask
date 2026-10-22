"""Join submission against in-memory ports: the member row is the request."""

import pytest
from conftest import Clock, FakeRateLimiter, FakeUnitOfWork, Store

from application.commands.join import SubmitJoinRequest, SubmitJoinRequestCommand
from domain.entities.member import Member
from domain.value_objects.access import JoinOutcome
from domain.value_objects.member_id import MemberId
from domain.value_objects.member_status import MemberStatus
from domain.value_objects.role import Role

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
    return SubmitJoinRequest(lambda: FakeUnitOfWork(store), limiter, clock.now)


def only_member(store: Store) -> Member:
    assert len(store.members) == 1
    return next(iter(store.members.values()))


def existing(store: Store, clock: Clock, status: MemberStatus) -> Member:
    member = Member.reconstitute(
        MemberId.new_id(),
        telegram_id=APPLICANT,
        role=Role.USER,
        status=status,
        created_at=clock.now(),
        display_name=None,
        access_version=3,
        status_changed_at=None,
        decided_by=None,
    )
    store.members[member.id] = member
    return member


async def test_a_new_person_becomes_a_pending_member(
    sut: SubmitJoinRequest, store: Store
) -> None:
    result = await sut.execute(SubmitJoinRequestCommand(APPLICANT, "Ann @ann"))

    assert result.value.outcome == "submitted"
    member = only_member(store)
    assert (member.telegram_id, member.role, member.status) == (
        APPLICANT,
        Role.USER,
        MemberStatus.PENDING,
    )
    assert member.display_name == "Ann @ann"
    assert member.access_version == 1
    assert result.value.member.id == member.id.value


async def test_a_second_request_while_pending_changes_nothing(
    sut: SubmitJoinRequest, store: Store
) -> None:
    await sut.execute(SubmitJoinRequestCommand(APPLICANT))
    before = only_member(store)
    before_state = (before.status, before.access_version, before.display_name)

    result = await sut.execute(SubmitJoinRequestCommand(APPLICANT, "Other name"))

    assert result.value.outcome == JoinOutcome.ALREADY_PENDING.value
    assert (only_member(store).status, only_member(store).access_version) == (
        before_state[0],
        before_state[1],
    )
    assert only_member(store).display_name == before_state[2]


async def test_an_approved_member_is_told_they_already_belong(
    sut: SubmitJoinRequest, store: Store, clock: Clock
) -> None:
    approved = existing(store, clock, MemberStatus.APPROVED)

    result = await sut.execute(SubmitJoinRequestCommand(APPLICANT))

    assert result.value.outcome == JoinOutcome.ALREADY_MEMBER.value
    assert only_member(store).status is approved.status


async def test_a_rejected_applicant_can_apply_again_with_a_new_version(
    sut: SubmitJoinRequest, store: Store, clock: Clock
) -> None:
    rejected = existing(store, clock, MemberStatus.REJECTED)
    rejected.decided_by = rejected.id.value

    result = await sut.execute(SubmitJoinRequestCommand(APPLICANT, "Ann"))

    assert result.value.outcome == JoinOutcome.SUBMITTED.value
    member = only_member(store)
    assert member.id == rejected.id
    assert member.status is MemberStatus.PENDING
    assert member.access_version == rejected.access_version + 1
    assert member.status_changed_at == clock.now()
    assert member.decided_by is None
    assert member.display_name == "Ann"


async def test_a_blocked_member_cannot_bypass_the_block(
    sut: SubmitJoinRequest, store: Store, clock: Clock
) -> None:
    blocked = existing(store, clock, MemberStatus.BLOCKED)

    result = await sut.execute(SubmitJoinRequestCommand(APPLICANT, "New name"))

    assert result.value.outcome == JoinOutcome.BLOCKED.value
    assert only_member(store).status is blocked.status


async def test_the_sixth_request_in_a_minute_is_rate_limited(
    sut: SubmitJoinRequest, store: Store
) -> None:
    for _ in range(5):
        await sut.execute(SubmitJoinRequestCommand(APPLICANT))

    result = await sut.execute(SubmitJoinRequestCommand(APPLICANT))

    assert result.is_err and result.error.code == "rate_limited"


async def test_the_limit_is_kept_per_telegram_id(
    sut: SubmitJoinRequest, limiter: FakeRateLimiter
) -> None:
    for _ in range(5):
        await sut.execute(SubmitJoinRequestCommand(APPLICANT))

    result = await sut.execute(SubmitJoinRequestCommand(APPLICANT + 1))

    assert result.value.outcome == JoinOutcome.SUBMITTED.value
    assert limiter.hits[f"join:{APPLICANT}"] == 5
