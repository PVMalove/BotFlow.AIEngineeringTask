"""One Identity transaction: repository wiring and atomic event staging."""

from collections.abc import Sequence

from kernel_platform.sqlalchemy_unit_of_work import EventHandler, SqlAlchemyUnitOfWork
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.repositories import (
    LoginCodeRepository,
    MemberRepository,
    OutboxRepository,
    SessionRepository,
)
from infrastructure.db.login_code_repository import SqlLoginCodes
from infrastructure.db.member_repository import SqlMembers
from infrastructure.db.outbox import SqlOutbox
from infrastructure.db.outbox_events import stage_identity_event
from infrastructure.db.session_repository import SqlSessions


class SqlUnitOfWork(SqlAlchemyUnitOfWork):
    members: MemberRepository
    login_codes: LoginCodeRepository
    sessions: SessionRepository
    outbox: OutboxRepository

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        event_handlers: Sequence[EventHandler] = (),
    ) -> None:
        super().__init__(
            session_factory,
            stage_outbox=stage_identity_event,
            event_handlers=event_handlers,
        )

    def _attach(self, session: AsyncSession) -> None:
        self.members = SqlMembers(session, self.track)
        self.login_codes = SqlLoginCodes(session)
        self.sessions = SqlSessions(session)
        self.outbox = SqlOutbox(session)
