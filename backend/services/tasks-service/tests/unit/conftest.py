"""In-memory ports of the tasks service: a board, repositories, a transaction."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

from kernel_domain.domain_event import DomainEvent
from kernel_platform.pagination import Cursor

from domain.entities.task import Task
from domain.repositories import InboxRepository, TaskRepository
from domain.value_objects.task_id import TaskId
from domain.value_objects.task_status import TaskStatus

logger = logging.getLogger(__name__)

EventHandler = Callable[[DomainEvent], Awaitable[None]]

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
AUTHOR = uuid4()


@dataclass
class Board:
    """Rows ordered like the SQL adapter: (created_at, id) descending."""

    tasks: dict[TaskId, Task] = field(default_factory=dict)
    inbox: set[str] = field(default_factory=set)
    events: list[DomainEvent] = field(default_factory=list)  # committed events

    def add(self, minutes: int, **overrides: object) -> Task:
        values: dict[str, object] = dict(
            text=f"task {minutes}",
            status=TaskStatus.PENDING,
            version=1,
            author_id=AUTHOR,
            author_telegram_id=1,
            author_name="Ann (@ann)",
            source_key=f"1:1:{uuid4()}",
            correlation_id=uuid4(),
            created_at=BASE + timedelta(minutes=minutes),
            updated_at=BASE + timedelta(minutes=minutes),
        )
        values.update(overrides)
        task = Task.reconstitute(TaskId(uuid4()), **values)  # type: ignore[arg-type]
        self.tasks[task.id] = task
        return task

    async def list_page(
        self, status: TaskStatus | None, after: Cursor | None, limit: int
    ) -> list[Task]:
        rows = sorted(
            self.tasks.values(),
            key=lambda t: (t.created_at, t.id.value),
            reverse=True,
        )
        if status is not None:
            rows = [t for t in rows if t.status is status]
        if after is not None:
            rows = [
                t
                for t in rows
                if (t.created_at, t.id.value) < (after.created_at, after.id)
            ]
        return rows[:limit]

    async def get(self, task_id: TaskId) -> Task | None:
        return self.tasks.get(task_id)


class FakeTaskRepository:
    def __init__(
        self, board: Board, track: list[Task], before_update: Callable[[], None]
    ) -> None:
        self._board = board
        self._track = track
        self._before_update = before_update

    async def get(self, task_id: TaskId) -> Task | None:
        stored = self._board.tasks.get(task_id)
        if stored is None:
            return None
        task = _copy(stored)  # a separate load, like a session of its own
        self._track.append(task)
        return task

    async def add(self, task: Task) -> bool:
        if any(t.source_key == task.source_key for t in self._board.tasks.values()):
            return False
        self._board.tasks[task.id] = _copy(task)
        self._track.append(task)
        return True

    async def update_status(self, task: Task, *, loaded_version: int) -> bool:
        self._before_update()
        stored = self._board.tasks[task.id]
        if stored.version != loaded_version:
            return False
        self._board.tasks[task.id] = _copy(task)
        return True


class FakeInbox:
    def __init__(self, board: Board) -> None:
        self._board = board

    async def claim(self, message_id: str) -> bool:
        if message_id in self._board.inbox:
            return False
        self._board.inbox.add(message_id)
        return True


class FakeTasksUnitOfWork:
    def __init__(
        self, board: Board, event_handlers: Sequence[EventHandler] = ()
    ) -> None:
        self._board = board
        self._event_handlers = tuple(event_handlers)
        self._tracked: list[Task] = []
        self.committed = False
        self.rolled_back = False
        self.before_update: Callable[[], None] | None = None  # a hook for races
        self.tasks: TaskRepository = FakeTaskRepository(
            board, self._tracked, self._run_before_update
        )
        self.inbox: InboxRepository = FakeInbox(board)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if not self.committed:
            self.rolled_back = True

    async def commit(self) -> None:
        self.committed = True
        events = [e for task in self._tracked for e in task.pull_events()]
        self._board.events.extend(events)
        # Like SqlAlchemyUnitOfWork: after the commit a failing handler is only logged.
        for event in events:
            for handler in self._event_handlers:
                try:
                    await handler(event)
                except Exception:
                    logger.warning("handler failed after commit", exc_info=False)

    async def rollback(self) -> None:
        self.rolled_back = True
        self.committed = False

    def _run_before_update(self) -> None:
        if self.before_update is not None:
            self.before_update()


def uuid_of(task: Task) -> UUID:
    return task.id.value


def _copy(task: Task) -> Task:
    return Task.reconstitute(
        task.id,
        text=task.text,
        status=task.status,
        version=task.version,
        author_id=task.author_id,
        author_telegram_id=task.author_telegram_id,
        author_name=task.author_name,
        source_key=task.source_key,
        correlation_id=task.correlation_id,
        created_at=task.created_at,
        updated_at=task.updated_at,
        source_type=task.source_type,
    )


class FakeBoardSubscription:
    """`wait` answers from a script (True: a signal, False: a quiet interval);
    when the script is over the connection stays quiet until it is closed."""

    def __init__(self, script: list[bool | Exception], timeouts: list[float]) -> None:
        self._script = script
        self._timeouts = timeouts

    async def wait(self, timeout: float) -> bool:
        self._timeouts.append(timeout)
        if self._script:
            step = self._script.pop(0)
            if isinstance(step, Exception):
                raise step
            return step
        await asyncio.Event().wait()
        return False


class FakeBoardFeed:
    def __init__(self, script: Sequence[bool | Exception] = ()) -> None:
        self.script = list(script)
        self.subscribe_error: Exception | None = None
        self.timeouts: list[float] = []
        self.subscribed = 0
        self.unsubscribed = 0

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[FakeBoardSubscription]:
        if self.subscribe_error is not None:
            raise self.subscribe_error
        self.subscribed += 1
        try:
            yield FakeBoardSubscription(self.script, self.timeouts)
        finally:
            self.unsubscribed += 1
