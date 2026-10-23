"""SQLAlchemy `UnitOfWork`: the transaction, and the domain events of its aggregates.

Repositories register every aggregate they load or save with `track`. On `commit`
the events of the tracked aggregates are staged into the outbox inside the
transaction and, once it is committed, handed to the in-process event handlers.
"""

import logging
from collections.abc import Awaitable, Callable, Sequence
from types import TracebackType
from typing import Protocol, Self

from kernel_domain.domain_event import DomainEvent
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

OutboxStager = Callable[[AsyncSession, DomainEvent], Awaitable[None]]
EventHandler = Callable[[DomainEvent], Awaitable[None]]


class EventSource(Protocol):
    def pull_events(self) -> list[DomainEvent]: ...


class SqlAlchemyUnitOfWork:
    """Opens a session per `async with`, rolls back unless `commit()` was called.

    A subclass builds its repositories in `_attach`, passing them `track`.
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        stage_outbox: OutboxStager | None = None,
        event_handlers: Sequence[EventHandler] = (),
    ) -> None:
        self._sessions = sessions
        self._stage_outbox = stage_outbox
        self._event_handlers = tuple(event_handlers)
        self._session: AsyncSession | None = None
        self._tracked: list[EventSource] = []
        self._committed = False

    async def __aenter__(self) -> Self:
        self._session = self._sessions()
        self._tracked = []
        self._committed = False
        self._attach(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        session = self._session
        if session is None:
            return
        try:
            if not self._committed:
                await session.rollback()
        finally:
            await session.close()
            self._session = None

    def track(self, entity: EventSource) -> None:
        if entity not in self._tracked:
            self._tracked.append(entity)

    async def commit(self) -> None:
        session = self._open_session()
        events = [event for entity in self._tracked for event in entity.pull_events()]
        if self._stage_outbox is not None:
            for event in events:
                await self._stage_outbox(session, event)
        await session.commit()
        self._committed = True
        await self._dispatch(events)

    async def rollback(self) -> None:
        await self._open_session().rollback()
        self._committed = False

    def _attach(self, session: AsyncSession) -> None:
        """Build the repositories of the session; overridden by a service."""

    def _open_session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("The unit of work is not entered.")
        return self._session

    async def _dispatch(self, events: Sequence[DomainEvent]) -> None:
        """After the commit the change is stored: a failing handler is only logged."""
        for event in events:
            for handler in self._event_handlers:
                try:
                    await handler(event)
                except Exception:
                    logger.warning(
                        "Handler of %s failed after commit",
                        type(event).__name__,
                        exc_info=False,
                    )
