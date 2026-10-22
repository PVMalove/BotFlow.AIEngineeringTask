"""Identity authentication use cases against in-memory ports."""

import hashlib
import re
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from conftest import Clock, FakeRateLimiter, FakeTokens, FakeUnitOfWork, Store

from kernel_domain.result import Result

from application.commands.auth import (
    EnsureInitialAdmin,
    ExchangeLoginCode,
    ExchangeLoginCodeCommand,
    IssueLoginCode,
    IssueLoginCodeCommand,
    Logout,
    LogoutCommand,
    SessionGrant,
)
from application.queries.members import (
    AuthenticateSession,
    AuthenticateSessionQuery,
    CurrentSession,
)
from contracts.views import LoginCodeView
from domain.entities.auth import Member, MemberStatus, Role

ADMIN_TELEGRAM_ID = 4_200_000_001


async def issued_by(issue: IssueLoginCode, telegram_id: int) -> LoginCodeView:
    result = await issue.execute(IssueLoginCodeCommand(telegram_id))
    assert result.is_ok
    return result.value


async def exchanged(
    exchange: ExchangeLoginCode, code: str, client: str = "c"
) -> Result[SessionGrant]:
    return await exchange.execute(ExchangeLoginCodeCommand(code, client))


async def authenticated(
    authenticate: AuthenticateSession, token: str
) -> Result[CurrentSession]:
    return await authenticate.execute(AuthenticateSessionQuery(token))


async def grant_for(
    issue: IssueLoginCode, exchange: ExchangeLoginCode, telegram_id: int
) -> SessionGrant:
    result = await exchanged(exchange, (await issued_by(issue, telegram_id)).code)
    assert result.is_ok
    return result.value


def code_of[T](result: Result[T]) -> str:
    assert result.is_err
    return result.error.code


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
    issued = await issued_by(issue, admin)

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
    issued = await issued_by(issue, admin)

    result = await exchanged(exchange, issued.code, "10.0.0.1")

    (session,) = store.sessions.values()
    grant = result.value
    assert grant.session.id == session.id
    assert grant.member.telegram_id == admin
    assert session.expires_at == clock.current + timedelta(hours=8)
    assert code_of(await exchanged(exchange, issued.code, "10.0.0.1")) == "invalid_code"
    assert len(store.sessions) == 1


async def test_new_code_cancels_the_previous_one(
    issue: IssueLoginCode, exchange: ExchangeLoginCode, admin: int
) -> None:
    first = await issued_by(issue, admin)
    second = await issued_by(issue, admin)

    assert code_of(await exchanged(exchange, first.code)) == "invalid_code"
    assert (await exchanged(exchange, second.code)).is_ok


async def test_expired_code_gives_no_session(
    issue: IssueLoginCode,
    exchange: ExchangeLoginCode,
    store: Store,
    clock: Clock,
    admin: int,
) -> None:
    issued = await issued_by(issue, admin)
    clock.advance(minutes=5, seconds=1)

    assert code_of(await exchanged(exchange, issued.code)) == "invalid_code"
    assert not store.sessions


@pytest.mark.parametrize("wrong", ["", "short", "x" * 32, "!" * 32, "x" * 33])
async def test_wrong_code_gives_no_session(
    exchange: ExchangeLoginCode, store: Store, admin: int, wrong: str
) -> None:
    assert code_of(await exchanged(exchange, wrong)) == "invalid_code"
    assert not store.sessions


@pytest.mark.parametrize("telegram_id", [1, ADMIN_TELEGRAM_ID])
async def test_code_is_not_issued_without_approved_membership(
    issue: IssueLoginCode, store: Store, admin: int, telegram_id: int
) -> None:
    if telegram_id == admin:
        (member,) = store.members.values()
        store.members[member.id] = member.with_status(MemberStatus.BLOCKED)

    result = await issue.execute(IssueLoginCodeCommand(telegram_id))

    assert code_of(result) == "not_approved"
    assert not store.codes


async def test_member_blocked_after_issue_cannot_exchange_and_code_is_burned(
    issue: IssueLoginCode,
    exchange: ExchangeLoginCode,
    store: Store,
    admin: int,
) -> None:
    issued = await issued_by(issue, admin)
    (member,) = store.members.values()
    store.members[member.id] = member.with_status(MemberStatus.BLOCKED)

    assert code_of(await exchanged(exchange, issued.code)) == "invalid_code"
    store.members[member.id] = member
    assert code_of(await exchanged(exchange, issued.code)) == "invalid_code"
    assert not store.sessions


async def test_code_issue_is_rate_limited_per_member(
    issue: IssueLoginCode, store: Store, admin: int
) -> None:
    for _ in range(5):
        await issued_by(issue, admin)

    result = await issue.execute(IssueLoginCodeCommand(admin))

    assert code_of(result) == "rate_limited"
    assert len(store.codes) == 5


async def test_exchange_attempts_are_rate_limited_per_client(
    exchange: ExchangeLoginCode, admin: int
) -> None:
    for _ in range(30):
        result = await exchanged(exchange, "x" * 32, "203.0.113.5")
        assert code_of(result) == "invalid_code"

    assert code_of(await exchanged(exchange, "x" * 32, "203.0.113.5")) == "rate_limited"
    assert (
        code_of(await exchanged(exchange, "x" * 32, "198.51.100.7")) == "invalid_code"
    )


async def test_session_authenticates_until_logout_revokes_it(
    issue: IssueLoginCode,
    exchange: ExchangeLoginCode,
    authenticate: AuthenticateSession,
    logout: Logout,
    admin: int,
) -> None:
    grant = await grant_for(issue, exchange, admin)

    current = (await authenticated(authenticate, grant.token)).value
    await logout.execute(LogoutCommand(current.session_id))

    assert current.member.telegram_id == admin
    assert code_of(await authenticated(authenticate, grant.token)) == "unauthenticated"


async def test_session_stops_working_after_8_hours(
    issue: IssueLoginCode,
    exchange: ExchangeLoginCode,
    authenticate: AuthenticateSession,
    clock: Clock,
    admin: int,
) -> None:
    grant = await grant_for(issue, exchange, admin)
    clock.advance(hours=7, minutes=59)
    assert (await authenticated(authenticate, grant.token)).is_ok

    clock.advance(minutes=1)

    assert code_of(await authenticated(authenticate, grant.token)) == "unauthenticated"


async def test_valid_token_does_not_outlive_membership_approval(
    issue: IssueLoginCode,
    exchange: ExchangeLoginCode,
    authenticate: AuthenticateSession,
    store: Store,
    admin: int,
) -> None:
    grant = await grant_for(issue, exchange, admin)
    (member,) = store.members.values()
    store.members[member.id] = member.with_status(MemberStatus.BLOCKED)

    assert code_of(await authenticated(authenticate, grant.token)) == "unauthenticated"


async def test_unknown_token_is_rejected(authenticate: AuthenticateSession) -> None:
    assert code_of(await authenticated(authenticate, "not-issued")) == "unauthenticated"


async def test_logout_of_unknown_session_is_harmless(logout: Logout) -> None:
    await logout.execute(LogoutCommand(uuid4()))


async def test_exchange_locks_the_member_before_it_touches_the_code(
    store: Store,
    clock: Clock,
    limiter: FakeRateLimiter,
    tokens: FakeTokens,
    ensure_admin: EnsureInitialAdmin,
    issue: IssueLoginCode,
) -> None:
    """A block must not interleave between spending a code and the session."""
    await ensure_admin.execute(ADMIN_TELEGRAM_ID)
    code = (await issued_by(issue, ADMIN_TELEGRAM_ID)).code
    calls: list[str] = []

    class Recording(FakeUnitOfWork):
        async def __aenter__(self) -> "Recording":
            await super().__aenter__()
            members, codes = self.members, self.login_codes

            class Members:
                async def get_by_login_code(
                    self, code_hash: str, *, lock: bool = False
                ) -> Member | None:
                    calls.append(f"member lock={lock}")
                    return await members.get_by_login_code(code_hash, lock=lock)

            class Codes:
                async def consume(self, code_hash: str, now: datetime) -> UUID | None:
                    calls.append("consume")
                    return await codes.consume(code_hash, now)

            self.members = Members()  # type: ignore[assignment]
            self.login_codes = Codes()  # type: ignore[assignment]
            return self

    sut = ExchangeLoginCode(lambda: Recording(store), limiter, clock, tokens)

    await sut.execute(ExchangeLoginCodeCommand(code, "ip"))

    assert calls == ["member lock=True", "consume"]
