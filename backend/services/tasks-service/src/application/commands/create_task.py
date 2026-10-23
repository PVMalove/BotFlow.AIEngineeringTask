"""CreateTask command: one source message becomes at most one Pending task."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from kernel_domain.result import Result
from kernel_platform.clock import utcnow

from domain.entities.task import Task
from domain.unit_of_work import TasksUnitOfWork
from domain.value_objects.source_key import source_key
from domain.value_objects.task_id import TaskId
from domain.value_objects.task_source import TaskSource

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CreateTaskCommand:
    message_id: str  # the broker message, for the inbox
    correlation_id: UUID  # the Automation job that asked for the task
    bot_id: int
    chat_id: int
    source_message_id: int
    text: str
    author_id: UUID
    author_telegram_id: int
    author_name: str
    source_type: TaskSource | None = None  # absent in messages of older senders


class CreateTaskCommandHandler:
    def __init__(
        self,
        uow_factory: Callable[[], TasksUnitOfWork],
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: CreateTaskCommand) -> Result[bool]:
        """True when the message was new; a redelivery changes nothing."""
        source = source_key(command.bot_id, command.chat_id, command.source_message_id)
        created = Task.create(
            TaskId.from_source(source),
            text=command.text,
            author_id=command.author_id,
            author_telegram_id=command.author_telegram_id,
            author_name=command.author_name,
            source_key=source,
            correlation_id=command.correlation_id,
            now=self._clock(),
            source_type=command.source_type,
        )
        if created.is_err:
            return Result[bool].fail(created.error)
        async with self._uow_factory() as uow:
            if not await uow.inbox.claim(command.message_id):
                return Result[bool].ok(False)  # the first copy already did everything
            # The unique source makes a second message for it a no-op: the task id
            # is derived from the source, so the repository stores one row and the
            # TaskCreated event, hence the result message, exists once.
            await uow.tasks.add(created.value)
            await uow.commit()  # the broker message is acknowledged only after this
        return Result[bool].ok(True)
