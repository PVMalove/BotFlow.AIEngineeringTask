"""Identity authentication use cases against in-memory ports."""

import hashlib
import re
from datetime import timedelta

import pytest
from conftest import Clock, FakeRateLimiter, FakeUnitOfWork, Store

from application.commands.auth import (
    EnsureInitialAdmin,
    ExchangeLoginCode,
    IssueLoginCode,
)
from domain.entities.auth import (
    InvalidLoginCode,
    MemberNotApproved,
    MemberStatus,
    RateLimited,
    Role,
)

ADMIN_TELEGRAM_ID = 4_200_000_001


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
def ensure_admin(store: Store, clock: Clock) -> EnsureInitialAdmin:
    return EnsureInitialAdmin(lambda: FakeUnitOfWork(store), clock)


@pytest.fixture
def issue(store: Store, clock: Clock, limiter: FakeRateLimiter) -> IssueLoginCode:
    return IssueLoginCode(lambda: FakeUnitOfWork(store), limiter, clock)


@pytest.fixture
def exchange(store: Store, clock: Clock, limiter: FakeRateLimiter) -> ExchangeLoginCode:
    return ExchangeLoginCode(lambda: FakeUnitOfWork(store), limiter, clock)


@pytest.fixture
async def admin(ensure_admin: EnsureInitialAdmin) -> int:
    await ensure_admin(ADMIN_TELEGRAM_ID)
    return ADMIN_TELEGRAM_ID


async def test_initial_admin_is_created_approved(
    ensure_admin: EnsureInitialAdmin, store: Store
) -> None:
    created = await ensure_admin(ADMIN_TELEGRAM_ID)

    (member,) = store.members.values()
    assert created is True
    assert member.telegram_id == ADMIN_TELEGRAM_ID
    assert (member.role, member.status) == (Role.ADMIN, MemberStatus.APPROVED)


async def test_repeat_keeps_existing_access_decision(
    ensure_admin: EnsureInitialAdmin, store: Store
) -> None:
    await ensure_admin(ADMIN_TELEGRAM_ID)
    (member,) = store.members.values()
    store.members[member.id] = member.with_status(MemberStatus.BLOCKED)

    created = await ensure_admin(ADMIN_TELEGRAM_ID)

    (after,) = store.members.values()
    assert created is False
    assert after.status is MemberStatus.BLOCKED
    assert after.id == member.id


async def test_code_is_32_url_safe_chars_valid_5_minutes_and_stored_hashed(
    issue: IssueLoginCode, store: Store, clock: Clock, admin: int
) -> None:
    issued = await issue(admin)

    assert re.fullmatch(r"[A-Za-z0-9_-]{32}", issued.code)
    assert issued.expires_at == clock.current + timedelta(minutes=5)
    (row,) = store.codes.values()
    assert row.code.code_hash == hashlib.sha256(issued.code.encode()).hexdigest()
    assert issued.code not in repr(row)


async def test_exchange_opens_an_8_hour_session_and_code_is_single_use(
    issue: IssueLoginCode,
    exchange: ExchangeLoginCode,
    store: Store,
    clock: Clock,
    admin: int,
) -> None:
    issued = await issue(admin)

    grant = await exchange(issued.code, client="10.0.0.1")

    (session,) = store.sessions.values()
    assert grant.session.id == session.id
    assert grant.member.telegram_id == admin
    assert session.expires_at == clock.current + timedelta(hours=8)
    with pytest.raises(InvalidLoginCode):
        await exchange(issued.code, client="10.0.0.1")
    assert len(store.sessions) == 1


async def test_new_code_cancels_the_previous_one(
    issue: IssueLoginCode, exchange: ExchangeLoginCode, admin: int
) -> None:
    first = await issue(admin)
    second = await issue(admin)

    with pytest.raises(InvalidLoginCode):
        await exchange(first.code, client="c")
    await exchange(second.code, client="c")


async def test_expired_code_gives_no_session(
    issue: IssueLoginCode,
    exchange: ExchangeLoginCode,
    store: Store,
    clock: Clock,
    admin: int,
) -> None:
    issued = await issue(admin)
    clock.advance(minutes=5, seconds=1)

    with pytest.raises(InvalidLoginCode):
        await exchange(issued.code, client="c")
    assert not store.sessions


@pytest.mark.parametrize("wrong", ["", "short", "x" * 32, "!" * 32, "x" * 33])
async def test_wrong_code_gives_no_session(
    exchange: ExchangeLoginCode, store: Store, admin: int, wrong: str
) -> None:
    with pytest.raises(InvalidLoginCode):
        await exchange(wrong, client="c")
    assert not store.sessions


@pytest.mark.parametrize("telegram_id", [1, ADMIN_TELEGRAM_ID])
async def test_code_is_not_issued_without_approved_membership(
    issue: IssueLoginCode, store: Store, admin: int, telegram_id: int
) -> None:
    if telegram_id == admin:
        (member,) = store.members.values()
        store.members[member.id] = member.with_status(MemberStatus.BLOCKED)

    with pytest.raises(MemberNotApproved):
        await issue(telegram_id)
    assert not store.codes


async def test_member_blocked_after_issue_cannot_exchange_and_code_is_burned(
    issue: IssueLoginCode,
    exchange: ExchangeLoginCode,
    store: Store,
    admin: int,
) -> None:
    issued = await issue(admin)
    (member,) = store.members.values()
    store.members[member.id] = member.with_status(MemberStatus.BLOCKED)

    with pytest.raises(InvalidLoginCode):
        await exchange(issued.code, client="c")
    store.members[member.id] = member
    with pytest.raises(InvalidLoginCode):
        await exchange(issued.code, client="c")
    assert not store.sessions


async def test_code_issue_is_rate_limited_per_member(
    issue: IssueLoginCode, store: Store, admin: int
) -> None:
    for _ in range(5):
        await issue(admin)

    with pytest.raises(RateLimited):
        await issue(admin)
    assert len(store.codes) == 5


async def test_exchange_attempts_are_rate_limited_per_client(
    exchange: ExchangeLoginCode, admin: int
) -> None:
    for _ in range(10):
        with pytest.raises(InvalidLoginCode):
            await exchange("x" * 32, client="203.0.113.5")

    with pytest.raises(RateLimited):
        await exchange("x" * 32, client="203.0.113.5")
    with pytest.raises(InvalidLoginCode):
        await exchange("x" * 32, client="198.51.100.7")
