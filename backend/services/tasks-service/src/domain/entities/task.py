from datetime import datetime
from uuid import UUID

from kernel_domain.entity import Entity
from kernel_domain.errors import Error, ErrorList
from kernel_domain.result import Result

from domain.errors import TaskErrors
from domain.events.task_domain_event import TaskCreated, TaskStatusChanged
from domain.value_objects.task_id import TaskId
from domain.value_objects.task_status import TaskStatus

MAX_TEXT = 4096
MAX_NAME = 64


class Task(Entity[TaskId]):
    """A card of the shared board.

    `create` accepts a new task, `reconstitute` rebuilds a stored one without
    validation or events. The version grows with every real change.
    """

    def __init__(
        self,
        id: TaskId,
        *,
        text: str,
        status: TaskStatus,
        version: int,
        author_id: UUID,
        author_telegram_id: int,
        author_name: str,  # snapshot taken when the task was created
        source_key: str,
        correlation_id: UUID,
        created_at: datetime,
        updated_at: datetime,
    ) -> None:
        super().__init__(id)
        self.text = text
        self.status = status
        self.version = version
        self.author_id = author_id
        self.author_telegram_id = author_telegram_id
        self.author_name = author_name
        self.source_key = source_key
        self.correlation_id = correlation_id
        self.created_at = created_at
        self.updated_at = updated_at

    @classmethod
    def create(
        cls,
        id: TaskId,
        *,
        text: str,
        author_id: UUID,
        author_telegram_id: int,
        author_name: str,
        source_key: str,
        correlation_id: UUID,
        now: datetime,
    ) -> Result["Task"]:
        error = _validate(text=text, author_name=author_name)
        if error is not None:
            return Result[Task].fail(error)
        task = cls(
            id,
            text=text,
            status=TaskStatus.PENDING,
            version=1,
            author_id=author_id,
            author_telegram_id=author_telegram_id,
            author_name=author_name,
            source_key=source_key,
            correlation_id=correlation_id,
            created_at=now,
            updated_at=now,
        )
        task.add_domain_event(
            TaskCreated(
                task_id=id, correlation_id=correlation_id, source_key=source_key
            )
        )
        return Result[Task].ok(task)

    @classmethod
    def reconstitute(
        cls,
        id: TaskId,
        *,
        text: str,
        status: TaskStatus,
        version: int,
        author_id: UUID,
        author_telegram_id: int,
        author_name: str,
        source_key: str,
        correlation_id: UUID,
        created_at: datetime,
        updated_at: datetime,
    ) -> "Task":
        return cls(
            id,
            text=text,
            status=status,
            version=version,
            author_id=author_id,
            author_telegram_id=author_telegram_id,
            author_name=author_name,
            source_key=source_key,
            correlation_id=correlation_id,
            created_at=created_at,
            updated_at=updated_at,
        )

    def change_status(
        self, target: TaskStatus, expected_version: int, now: datetime
    ) -> Result[bool]:
        """Move to any status. A stale version is always a conflict, even when the
        target equals the current status; the current version and status make a
        no-op that changes nothing and raises no event (`ok(False)`)."""
        if expected_version != self.version:
            return Result[bool].fail(TaskErrors.version_conflict())
        if target is self.status:
            return Result[bool].ok(False)
        previous = self.status
        self.status = target
        self.version += 1
        self.updated_at = now
        self.add_domain_event(
            TaskStatusChanged(
                task_id=self.id,
                previous_status=previous,
                status=target,
                version=self.version,
            )
        )
        return Result[bool].ok(True)


def _validate(*, text: str, author_name: str) -> Error | None:
    errors: list[Error] = []
    if not 1 <= len(text) <= MAX_TEXT:
        errors.append(TaskErrors.invalid_text(MAX_TEXT))
    if not 1 <= len(author_name) <= MAX_NAME:
        errors.append(TaskErrors.invalid_author_name(MAX_NAME))
    return ErrorList.of(errors) if errors else None
