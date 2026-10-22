"""Identity authentication use cases against in-memory ports."""

import hashlib
import re
from datetime import timedelta
from uuid import uuid4

import pytest
from conftest import Clock, FakeRateLimiter, FakeTokens, FakeUnitOfWork, Store

from application.commands.auth import (
    EnsureInitialAdmin,
    ExchangeLoginCode,
    IssueLoginCode,
    Logout,
)
from application.queries.members import AuthenticateSession
from domain.entities.auth import (
    InvalidLoginCode,
    InvalidSession,
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
def tokens() -> FakeTokens:
    return FakeTokens()


@pytest.fixture
def exchange(
    store: Store, clock: Clock, limiter: FakeRateLimiter, tokens: FakeTokens
) -> ExchangeLoginCode:
    return ExchangeLoginCode(lambda: FakeUnitOfWork(store), limiter, clock, tokens)


@pytest.fixture
def logout(store: Store, clock: Clock) -> Logout:
    return Logout(lambda: FakeUnitOfWork(store), clock)


@pytest.fixture
def authenticate(store: Store, clock: Clock, tokens: FakeTokens) -> AuthenticateSession:
    return AuthenticateSession(lambda: FakeUnitOfWork(store), tokens, clock)


@pytest.fixture
async def admin(ensure_admin: EnsureInitialAdmin) -> int:
    await ensure_admin.execute(ADMIN_TELEGRAM_ID)
    return ADMIN_TELEGRAM_ID


async def test_initial_admin_is_created_approved(
    ensure_admin: EnsureInitialAdmin, store: Store
) -> None:
    created = await ensure_admin.execute(ADMIN_TELEGRAM_ID)

    (member,) = store.members.values()
    assert created is True
    assert member.telegram_id == ADMIN_TELEGRAM_ID
    assert (member.role, member.status) == (Role.ADMIN, MemberStatus.APPROVED)


async def test_repeat_keeps_existing_access_decision(
    ensure_admin: EnsureInitialAdmin, store: Store
) -> None:
    await ensure_admin.execute(ADMIN_TELEGRAM_ID)
    (member,) = store.members.values()
    store.members[member.id] = member.with_status(MemberStatus.BLOCKED)

    created = await ensure_admin.execute(ADMIN_TELEGRAM_ID)

    (after,) = store.members.values()
    assert created is False
    assert after.status is MemberStatus.BLOCKED
    assert after.id == member.id


async def test_code_is_32_url_safe_chars_valid_5_minutes_and_stored_hashed(
    issue: IssueLoginCode, store: Store, clock: Clock, admin: int
) -> None:
    issued = await issue.execute(admin)

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
    issued = await issue.execute(admin)

    grant = await exchange.execute(issued.code, client="10.0.0.1")

    (session,) = store.sessions.values()
    assert grant.session.id == session.id
    assert grant.member.telegram_id == admin
    assert session.expires_at == clock.current + timedelta(hours=8)
    with pytest.raises(InvalidLoginCode):
        await exchange.execute(issued.code, client="10.0.0.1")
    assert len(store.sessions) == 1


async def test_new_code_cancels_the_previous_one(
    issue: IssueLoginCode, exchange: ExchangeLoginCode, admin: int
) -> None:
    first = await issue.execute(admin)
    second = await issue.execute(admin)

    with pytest.raises(InvalidLoginCode):
        await exchange.execute(first.code, client="c")
    await exchange.execute(second.code, client="c")


async def test_expired_code_gives_no_session(
    issue: IssueLoginCode,
    exchange: ExchangeLoginCode,
    store: Store,
    clock: Clock,
    admin: int,
) -> None:
    issued = await issue.execute(admin)
    clock.advance(minutes=5, seconds=1)

    with pytest.raises(InvalidLoginCode):
        await exchange.execute(issued.code, client="c")
    assert not store.sessions


@pytest.mark.parametrize("wrong", ["", "short", "x" * 32, "!" * 32, "x" * 33])
async def test_wrong_code_gives_no_session(
    exchange: ExchangeLoginCode, store: Store, admin: int, wrong: str
) -> None:
    with pytest.raises(InvalidLoginCode):
        await exchange.execute(wrong, client="c")
    assert not store.sessions


@pytest.mark.parametrize("telegram_id", [1, ADMIN_TELEGRAM_ID])
async def test_code_is_not_issued_without_approved_membership(
    issue: IssueLoginCode, store: Store, admin: int, telegram_id: int
) -> None:
    if telegram_id == admin:
        (member,) = store.members.values()
        store.members[member.id] = member.with_status(MemberStatus.BLOCKED)

    with pytest.raises(MemberNotApproved):
        await issue.execute(telegram_id)
    assert not store.codes


async def test_member_blocked_after_issue_cannot_exchange_and_code_is_burned(
    issue: IssueLoginCode,
    exchange: ExchangeLoginCode,
    store: Store,
    admin: int,
) -> None:
    issued = await issue.execute(admin)
    (member,) = store.members.values()
    store.members[member.id] = member.with_status(MemberStatus.BLOCKED)

    with pytest.raises(InvalidLoginCode):
        await exchange.execute(issued.code, client="c")
    store.members[member.id] = member
    with pytest.raises(InvalidLoginCode):
        await exchange.execute(issued.code, client="c")
    assert not store.sessions


async def test_code_issue_is_rate_limited_per_member(
    issue: IssueLoginCode, store: Store, admin: int
) -> None:
    for _ in range(5):
        await issue.execute(admin)

    with pytest.raises(RateLimited):
        await issue.execute(admin)
    assert len(store.codes) == 5


async def test_exchange_attempts_are_rate_limited_per_client(
    exchange: ExchangeLoginCode, admin: int
) -> None:
    for _ in range(30):
        with pytest.raises(InvalidLoginCode):
            await exchange.execute("x" * 32, client="203.0.113.5")

    with pytest.raises(RateLimited):
        await exchange.execute("x" * 32, client="203.0.113.5")
    with pytest.raises(InvalidLoginCode):
        await exchange.execute("x" * 32, client="198.51.100.7")


async def test_session_authenticates_until_logout_revokes_it(
    issue: IssueLoginCode,
    exchange: ExchangeLoginCode,
    authenticate: AuthenticateSession,
    logout: Logout,
    admin: int,
) -> None:
    grant = await exchange.execute((await issue.execute(admin)).code, client="c")

    current = await authenticate.execute(grant.token)
    await logout.execute(current.session_id)

    assert current.member.telegram_id == admin
    with pytest.raises(InvalidSession):
        await authenticate.execute(grant.token)


async def test_session_stops_working_after_8_hours(
    issue: IssueLoginCode,
    exchange: ExchangeLoginCode,
    authenticate: AuthenticateSession,
    clock: Clock,
    admin: int,
) -> None:
    grant = await exchange.execute((await issue.execute(admin)).code, client="c")
    clock.advance(hours=7, minutes=59)
    await authenticate.execute(grant.token)

    clock.advance(minutes=1)

    with pytest.raises(InvalidSession):
        await authenticate.execute(grant.token)


async def test_valid_token_does_not_outlive_membership_approval(
    issue: IssueLoginCode,
    exchange: ExchangeLoginCode,
    authenticate: AuthenticateSession,
    store: Store,
    admin: int,
) -> None:
    grant = await exchange.execute((await issue.execute(admin)).code, client="c")
    (member,) = store.members.values()
    store.members[member.id] = member.with_status(MemberStatus.BLOCKED)

    with pytest.raises(InvalidSession):
        await authenticate.execute(grant.token)


async def test_unknown_token_is_rejected(authenticate: AuthenticateSession) -> None:
    with pytest.raises(InvalidSession):
        await authenticate.execute("not-issued")


async def test_logout_of_unknown_session_is_harmless(logout: Logout) -> None:
    await logout.execute(uuid4())
