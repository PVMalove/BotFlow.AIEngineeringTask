"""Member and session queries."""

from dataclasses import dataclass
from uuid import UUID

from kernel_domain.result import Result

from application.ports import Clock, TokenService, UnitOfWorkFactory
from contracts.views import AdminMemberView, MemberView
from domain.entities.member import Member
from domain.errors import IdentityErrors
from domain.value_objects.member_id import MemberId
from domain.value_objects.member_status import MemberStatus


@dataclass(frozen=True, slots=True)
class CurrentSession:
    session_id: UUID
    member: Member


@dataclass(frozen=True, slots=True)
class AuthenticateSessionQuery:
    """Параметры проверки и получения активной сессии."""

    token: str


class AuthenticateSessionQueryHandler:
    """A valid signature is not enough: the stored session and access are rechecked."""

    def __init__(
        self, uow_factory: UnitOfWorkFactory, tokens: TokenService, clock: Clock
    ) -> None:
        self._uow_factory = uow_factory
        self._tokens = tokens
        self._clock = clock

    async def execute(self, query: AuthenticateSessionQuery) -> Result[CurrentSession]:
        claims = self._tokens.verify(query.token)
        if claims.is_err:
            return Result[CurrentSession].fail(claims.error)
        async with self._uow_factory() as uow:
            session = await uow.sessions.get(claims.value.session_id)
            if session is None or not session.is_usable(
                claims.value.member_id, self._clock()
            ):
                return Result[CurrentSession].fail(IdentityErrors.invalid_session())
            member = await uow.members.get(MemberId(session.member_id))
        if member is None or member.status is not MemberStatus.APPROVED:
            return Result[CurrentSession].fail(IdentityErrors.invalid_session())
        return Result[CurrentSession].ok(
            CurrentSession(session_id=session.id, member=member)
        )


@dataclass(frozen=True, slots=True)
class FindMemberByTelegramQuery:
    """Параметры поиска участника по идентификатору Telegram."""

    telegram_id: int


class FindMemberByTelegramQueryHandler:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: FindMemberByTelegramQuery) -> Result[MemberView]:
        async with self._uow_factory() as uow:
            member = await uow.members.get_by_telegram_id(query.telegram_id)
        if member is None:
            return Result[MemberView].fail(IdentityErrors.member_not_found())
        return Result[MemberView].ok(MemberView.from_domain(member))


@dataclass(frozen=True, slots=True)
class ListMembersQuery:
    """Страница участников или заявок с отбором по статусам."""

    statuses: tuple[MemberStatus, ...]
    limit: int = 50
    offset: int = 0


class ListMembersQueryHandler:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: ListMembersQuery) -> Result[list[AdminMemberView]]:
        async with self._uow_factory() as uow:
            found = await uow.members.list_by_status(
                query.statuses, limit=query.limit, offset=query.offset
            )
        return Result[list[AdminMemberView]].ok(
            [AdminMemberView.from_domain(member) for member in found]
        )


__all__ = [
    "ListMembersQueryHandler",
    "ListMembersQuery",
    "AuthenticateSessionQueryHandler",
    "AuthenticateSessionQuery",
    "CurrentSession",
    "FindMemberByTelegramQueryHandler",
    "FindMemberByTelegramQuery",
]
