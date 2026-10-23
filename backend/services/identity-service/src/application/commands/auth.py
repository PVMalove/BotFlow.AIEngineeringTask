"""Authentication commands."""

import hashlib
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from kernel_domain.result import Result

from application.ports import Clock, RateLimiter, TokenService, UnitOfWorkFactory
from contracts.views import LoginCodeView
from domain.entities.auth_session import AuthSession
from domain.entities.login_code import LoginCode
from domain.entities.member import Member
from domain.errors import IdentityErrors
from domain.value_objects.member_id import MemberId
from domain.value_objects.member_status import MemberStatus

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


class EnsureInitialAdminCommandHandler:
    """Create the first admin once; an existing member is never changed."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: EnsureInitialAdminCommand) -> bool:
        member = Member.initial_admin(
            MemberId.new_id(), telegram_id=command.telegram_id, now=self._clock()
        )
        async with self._uow_factory() as uow:
            created = await uow.members.add_if_absent(member)
            await uow.commit()
        return created


@dataclass(frozen=True, slots=True)
class IssueLoginCodeCommand:
    """Параметры выпуска одноразового кода авторизации."""

    telegram_id: int


class IssueLoginCodeCommandHandler:
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

    async def execute(self, command: IssueLoginCodeCommand) -> Result[LoginCodeView]:
        telegram_id = command.telegram_id
        if not await self._limiter.allow(f"issue:{telegram_id}", self._attempts, 60):
            return Result[LoginCodeView].fail(IdentityErrors.rate_limited())
        async with self._uow_factory() as uow:
            member = await uow.members.get_by_telegram_id(telegram_id, lock=True)
            if member is None or member.status is not MemberStatus.APPROVED:
                return Result[LoginCodeView].fail(IdentityErrors.member_not_approved())
            now = self._clock()
            code = self._generate_code()
            await uow.login_codes.cancel_active(member.id.value, now)
            await uow.login_codes.add(
                LoginCode(
                    uuid4(),
                    member_id=member.id.value,
                    code_hash=hash_login_code(code),
                    created_at=now,
                    expires_at=now + LOGIN_CODE_TTL,
                )
            )
            await uow.commit()
        return Result[LoginCodeView].ok(
            LoginCodeView(code=code, expires_at=now + LOGIN_CODE_TTL)
        )


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


class ExchangeLoginCodeCommandHandler:
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

    async def execute(self, command: ExchangeLoginCodeCommand) -> Result[SessionGrant]:
        code = command.code
        if not await self._limiter.allow(
            f"exchange:{command.client}", self._attempts, 60
        ):
            return Result[SessionGrant].fail(IdentityErrors.rate_limited())
        if not _CODE_PATTERN.fullmatch(code):
            return Result[SessionGrant].fail(IdentityErrors.invalid_login_code())
        async with self._uow_factory() as uow:
            now = self._clock()
            code_hash = hash_login_code(code)
            # Member row first, like block and issuance: a session can then not
            # be created concurrently with a block that would have revoked it.
            member = await uow.members.get_by_login_code(code_hash, lock=True)
            if member is None:
                return Result[SessionGrant].fail(IdentityErrors.invalid_login_code())
            if await uow.login_codes.consume(code_hash, now) is None:
                return Result[SessionGrant].fail(IdentityErrors.invalid_login_code())
            if member.status is not MemberStatus.APPROVED:
                await uow.commit()  # the code is spent either way
                return Result[SessionGrant].fail(IdentityErrors.invalid_login_code())
            session = AuthSession(
                uuid4(),
                member_id=member.id.value,
                created_at=now,
                expires_at=now + SESSION_TTL,
            )
            await uow.sessions.add(session)
            await uow.commit()
        return Result[SessionGrant].ok(
            SessionGrant(
                session=session, member=member, token=self._tokens.issue(session)
            )
        )


@dataclass(frozen=True, slots=True)
class LogoutCommand:
    """Параметры отзыва сессии."""

    session_id: UUID


class LogoutCommandHandler:
    """Revoke one session; there is no refresh to cancel."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: LogoutCommand) -> None:
        async with self._uow_factory() as uow:
            await uow.sessions.revoke(command.session_id, self._clock())
            await uow.commit()


__all__ = [
    "EnsureInitialAdminCommandHandler",
    "EnsureInitialAdminCommand",
    "ExchangeLoginCodeCommandHandler",
    "ExchangeLoginCodeCommand",
    "IssueLoginCodeCommandHandler",
    "IssueLoginCodeCommand",
    "LOGIN_CODE_TTL",
    "LoginCodeView",
    "LogoutCommandHandler",
    "LogoutCommand",
    "SESSION_TTL",
    "SessionGrant",
    "hash_login_code",
    "new_login_code",
]
