"""The automation transaction on SQLAlchemy: repositories, outbox staging, handlers."""

from collections.abc import Callable, Sequence
from datetime import datetime

from kernel_platform.clock import utcnow
from kernel_platform.inbox import SqlInboxRepository
from kernel_platform.sqlalchemy_unit_of_work import EventHandler, SqlAlchemyUnitOfWork
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.repositories import InboxRepository, IntentRepository, JobRepository
from infrastructure.db.intent_repository import SqlIntentRepository
from infrastructure.db.job_repository import SqlJobRepository
from infrastructure.db.models import InboxMessageModel
from infrastructure.db.outbox_events import stage_automation_event


class SqlAutomationUnitOfWork(SqlAlchemyUnitOfWork):
    jobs: JobRepository
    intents: IntentRepository
    inbox: InboxRepository

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        event_handlers: Sequence[EventHandler] = (),
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        super().__init__(
            sessions,
            stage_outbox=stage_automation_event,
            event_handlers=event_handlers,
        )
        self._clock = clock

    def _attach(self, session: AsyncSession) -> None:
        self.jobs = SqlJobRepository(session, self.track)
        self.intents = SqlIntentRepository(session, self.track, self._clock)
        self.inbox = SqlInboxRepository(session, self._clock, InboxMessageModel)
