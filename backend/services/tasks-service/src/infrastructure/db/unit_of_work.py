"""The tasks transaction on SQLAlchemy: repositories, outbox staging, event handlers."""

from collections.abc import Callable, Sequence
from datetime import datetime

from kernel_platform.clock import utcnow
from kernel_platform.sqlalchemy_unit_of_work import EventHandler, SqlAlchemyUnitOfWork
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.repositories import InboxRepository, TaskRepository
from infrastructure.db.outbox_events import stage_task_event
from infrastructure.db.task_repository import SqlInboxRepository, SqlTaskRepository


class SqlTasksUnitOfWork(SqlAlchemyUnitOfWork):
    tasks: TaskRepository
    inbox: InboxRepository

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        event_handlers: Sequence[EventHandler] = (),
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        super().__init__(
            sessions, stage_outbox=stage_task_event, event_handlers=event_handlers
        )
        self._clock = clock

    def _attach(self, session: AsyncSession) -> None:
        self.tasks = SqlTaskRepository(session, self.track)
        self.inbox = SqlInboxRepository(session, self._clock)
