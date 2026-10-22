"""Member and session queries."""

from dataclasses import dataclass
from uuid import UUID

from application.ports import Clock, TokenService, UnitOfWorkFactory
from contracts.views import MemberView
from domain.entities.auth import InvalidSession, Member, MemberStatus


def member_view(member: Member) -> MemberView:
    return MemberView(
        id=member.id,
        telegram_id=member.telegram_id,
        role=member.role.value,
        status=member.status.value,
    )


@dataclass(frozen=True, slots=True)
class CurrentSession:
    session_id: UUID
    member: Member


class AuthenticateSession:
    """A valid signature is not enough: the stored session and access are rechecked."""

    def __init__(
        self, uow_factory: UnitOfWorkFactory, tokens: TokenService, clock: Clock
    ) -> None:
        self._uow_factory = uow_factory
        self._tokens = tokens
        self._clock = clock

    async def __call__(self, token: str) -> CurrentSession:
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
