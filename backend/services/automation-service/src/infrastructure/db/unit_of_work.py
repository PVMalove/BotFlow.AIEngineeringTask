"""The automation transaction on SQLAlchemy: repositories, outbox staging, handlers."""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from kernel_platform.unit_of_work import EventHandler, SqlAlchemyUnitOfWork
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.repositories import InboxRepository, IntentRepository, JobRepository
from infrastructure.db.intent_repository import SqlInbox, SqlIntentRepository
from infrastructure.db.job_repository import SqlJobRepository
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
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
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
        self.inbox = SqlInbox(session, self._clock)
