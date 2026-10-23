from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from kernel_domain.value_object import ValueObject

# A fixed namespace makes the id a pure function of the source, so a redelivered
# or replayed command always addresses the same task.
_TASK_NAMESPACE = UUID("6f0c0b5e-3a54-4c5b-9d0e-1d2a7b7c9e01")


@dataclass(frozen=True, eq=False)
class TaskId(ValueObject):
    value: UUID

    @classmethod
    def from_source(cls, source_key: str) -> "TaskId":
        """The task of one Telegram message; unique per source."""
        return cls(uuid5(_TASK_NAMESPACE, source_key))

    def _equality_components(self) -> tuple[Any, ...]:
        return (self.value,)
