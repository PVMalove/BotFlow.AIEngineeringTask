"""Member and session queries."""

from dataclasses import dataclass
from uuid import UUID

from application.ports import Clock, TokenService, UnitOfWorkFactory
from contracts.views import AdminMemberView, MemberView
from domain.entities.auth import InvalidSession, Member, MemberStatus


def member_view(member: Member) -> MemberView:
    return MemberView(
        id=member.id,
        telegram_id=member.telegram_id,
        role=member.role.value,
        status=member.status.value,
        access_version=member.access_version,
    )


def admin_member_view(member: Member) -> AdminMemberView:
    return AdminMemberView(
        id=member.id,
        telegram_id=member.telegram_id,
        display_name=member.display_name,
        role=member.role.value,
        status=member.status.value,
        access_version=member.access_version,
        created_at=member.created_at,
        status_changed_at=member.status_changed_at,
    )


@dataclass(frozen=True, slots=True)
class CurrentSession:
    session_id: UUID
    member: Member


@dataclass(frozen=True, slots=True)
class AuthenticateSessionQuery:
    """Параметры проверки и получения активной сессии."""

    token: str


class AuthenticateSession:
    """A valid signature is not enough: the stored session and access are rechecked."""

    def __init__(
        self, uow_factory: UnitOfWorkFactory, tokens: TokenService, clock: Clock
    ) -> None:
        self._uow_factory = uow_factory
        self._tokens = tokens
        self._clock = clock

    async def execute(self, query: AuthenticateSessionQuery | str) -> CurrentSession:
        token = query.token if isinstance(query, AuthenticateSessionQuery) else query
        claims = self._tokens.verify(token)
        async with self._uow_factory() as uow:
            session = await uow.sessions.get(claims.session_id)
            if (
                session is None
                or session.member_id != claims.member_id
                or session.revoked_at is not None
                or session.expires_at <= self._clock()
            ):
                raise InvalidSession
            member = await uow.members.get(session.member_id)
        if member is None or member.status is not MemberStatus.APPROVED:
            raise InvalidSession
        return CurrentSession(session_id=session.id, member=member)


@dataclass(frozen=True, slots=True)
class FindMemberByTelegramQuery:
    """Параметры поиска участника по идентификатору Telegram."""

    telegram_id: int


class FindMemberByTelegram:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self, query: FindMemberByTelegramQuery | int
    ) -> MemberView | None:
        telegram_id = (
            query.telegram_id if isinstance(query, FindMemberByTelegramQuery) else query
        )
        async with self._uow_factory() as uow:
            member = await uow.members.get_by_telegram_id(telegram_id)
        return member_view(member) if member else None


@dataclass(frozen=True, slots=True)
class ListMembersQuery:
    """Страница участников или заявок с отбором по статусам."""

    statuses: tuple[MemberStatus, ...]
    limit: int = 50
    offset: int = 0


class ListMembers:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, query: ListMembersQuery) -> list[AdminMemberView]:
        async with self._uow_factory() as uow:
            found = await uow.members.list_by_status(
                query.statuses, limit=query.limit, offset=query.offset
            )
        return [admin_member_view(member) for member in found]


__all__ = [
    "ListMembers",
    "ListMembersQuery",
    "admin_member_view",
    "AuthenticateSession",
    "AuthenticateSessionQuery",
    "CurrentSession",
    "FindMemberByTelegram",
    "FindMemberByTelegramQuery",
    "member_view",
]
