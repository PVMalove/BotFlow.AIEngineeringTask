"""Authentication commands."""

import hashlib
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from application.ports import Clock, RateLimiter, TokenService, UnitOfWorkFactory
from contracts.views import LoginCodeView
from domain.entities.auth import (
    AuthSession,
    InvalidLoginCode,
    LoginCode,
    Member,
    MemberNotApproved,
    MemberStatus,
    RateLimited,
    Role,
)

LOGIN_CODE_TTL = timedelta(minutes=5)
SESSION_TTL = timedelta(hours=8)
_CODE_PATTERN = re.compile(r"[A-Za-z0-9_-]{32}")


def new_login_code() -> str:
    """32 URL-safe characters carrying 192 bits of randomness."""
    return secrets.token_urlsafe(24)


def hash_login_code(code: str) -> str:
    # The code is high-entropy, so a fast digest is enough to protect it at rest.
    return hashlib.sha256(code.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class EnsureInitialAdminCommand:
    """Параметры создания первого администратора."""

    telegram_id: int


class EnsureInitialAdmin:
    """Create the first admin once; an existing member is never changed."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: EnsureInitialAdminCommand | int) -> bool:
        telegram_id = (
            command.telegram_id
            if isinstance(command, EnsureInitialAdminCommand)
            else command
        )
        member = Member(
            id=uuid4(),
            telegram_id=telegram_id,
            role=Role.ADMIN,
            status=MemberStatus.APPROVED,
            created_at=self._clock(),
        )
        async with self._uow_factory() as uow:
            created = await uow.members.add_if_absent(member)
            await uow.commit()
        return created


@dataclass(frozen=True, slots=True)
class IssueLoginCodeCommand:
    """Параметры выпуска одноразового кода авторизации."""

    telegram_id: int


class IssueLoginCode:
    """Give an approved member a fresh code; any earlier live code is cancelled."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        limiter: RateLimiter,
        clock: Clock,
        generate_code: Callable[[], str] = new_login_code,
        attempts_per_minute: int = 5,
    ) -> None:
        self._uow_factory = uow_factory
        self._limiter = limiter
        self._clock = clock
        self._generate_code = generate_code
        self._attempts = attempts_per_minute

    async def execute(self, command: IssueLoginCodeCommand | int) -> LoginCodeView:
        telegram_id = (
            command.telegram_id
            if isinstance(command, IssueLoginCodeCommand)
            else command
        )
        if not await self._limiter.allow(f"issue:{telegram_id}", self._attempts, 60):
            raise RateLimited
        async with self._uow_factory() as uow:
            member = await uow.members.get_by_telegram_id(telegram_id, lock=True)
            if member is None or member.status is not MemberStatus.APPROVED:
                raise MemberNotApproved
            now = self._clock()
            code = self._generate_code()
            await uow.login_codes.cancel_active(member.id, now)
            await uow.login_codes.add(
                LoginCode(
                    id=uuid4(),
                    member_id=member.id,
                    code_hash=hash_login_code(code),
                    created_at=now,
                    expires_at=now + LOGIN_CODE_TTL,
                )
            )
            await uow.commit()
        return LoginCodeView(code=code, expires_at=now + LOGIN_CODE_TTL)


@dataclass(frozen=True, slots=True)
class SessionGrant:
    session: AuthSession
    member: Member
    token: str


@dataclass(frozen=True, slots=True)
class ExchangeLoginCodeCommand:
    """Параметры обмена кода на сессию."""

    code: str
    client: str


class ExchangeLoginCode:
    """Trade a live code for a session; the code works exactly once."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        limiter: RateLimiter,
        clock: Clock,
        tokens: TokenService,
        attempts_per_minute: int = 30,
    ) -> None:
        self._uow_factory = uow_factory
        self._limiter = limiter
        self._clock = clock
        self._tokens = tokens
        self._attempts = attempts_per_minute

    async def execute(
        self,
        command: ExchangeLoginCodeCommand | str,
        *,
        client: str | None = None,
    ) -> SessionGrant:
        if isinstance(command, ExchangeLoginCodeCommand):
            code = command.code
            client_str = command.client
        else:
            code = command
            client_str = client or "unknown"
        if not await self._limiter.allow(f"exchange:{client_str}", self._attempts, 60):
            raise RateLimited
        if not _CODE_PATTERN.fullmatch(code):
            raise InvalidLoginCode
        async with self._uow_factory() as uow:
            now = self._clock()
            member_id = await uow.login_codes.consume(hash_login_code(code), now)
            if member_id is None:
                raise InvalidLoginCode
            member = await uow.members.get(member_id)
            if member is None or member.status is not MemberStatus.APPROVED:
                await uow.commit()  # the code is spent either way
                raise InvalidLoginCode
            session = AuthSession(
                id=uuid4(),
                member_id=member.id,
                created_at=now,
                expires_at=now + SESSION_TTL,
            )
            await uow.sessions.add(session)
            await uow.commit()
        return SessionGrant(
            session=session, member=member, token=self._tokens.issue(session)
        )


@dataclass(frozen=True, slots=True)
class LogoutCommand:
    """Параметры отзыва сессии."""

    session_id: UUID


class Logout:
    """Revoke one session; there is no refresh to cancel."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: LogoutCommand | UUID) -> None:
        session_id = (
            command.session_id if isinstance(command, LogoutCommand) else command
        )
        async with self._uow_factory() as uow:
            await uow.sessions.revoke(session_id, self._clock())
            await uow.commit()


__all__ = [
    "EnsureInitialAdmin",
    "EnsureInitialAdminCommand",
    "ExchangeLoginCode",
    "ExchangeLoginCodeCommand",
    "IssueLoginCode",
    "IssueLoginCodeCommand",
    "LOGIN_CODE_TTL",
    "LoginCodeView",
    "Logout",
    "LogoutCommand",
    "SESSION_TTL",
    "SessionGrant",
    "hash_login_code",
    "new_login_code",
]
