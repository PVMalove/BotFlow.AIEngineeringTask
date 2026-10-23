from dataclasses import dataclass
from typing import Any
from uuid import UUID

from kernel_domain.domain_event import DomainEvent

from domain.value_objects.task_id import TaskId
from domain.value_objects.task_status import TaskStatus


@dataclass(frozen=True, kw_only=True)
class TaskEvent(DomainEvent):
    """Common ancestor of the task events: they all name their task."""

    aggregate_type: str = "Task"

    task_id: TaskId

    def aggregate_id(self) -> UUID:
        return self.task_id.value

    def to_payload(self) -> dict[str, Any]:
        return {"task_id": str(self.task_id.value)}


@dataclass(frozen=True, kw_only=True)
class TaskCreated(TaskEvent):
    event_type: str = "task.created"

    correlation_id: UUID  # the Automation job that asked for the task
    source_key: str

    def to_payload(self) -> dict[str, Any]:
        return {
            **super().to_payload(),
            "job_id": str(self.correlation_id),
            "source_key": self.source_key,
        }


@dataclass(frozen=True, kw_only=True)
class TaskStatusChanged(TaskEvent):
    event_type: str = "task.status-changed"

    previous_status: TaskStatus
    status: TaskStatus
    version: int

    def to_payload(self) -> dict[str, Any]:
        return {
            **super().to_payload(),
            "previous_status": self.previous_status.value,
            "status": self.status.value,
            "version": self.version,
        }
