from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from kernel_domain.value_object import ValueObject

# A fixed namespace makes the id a pure function of the source message, so a
# redelivered or replayed update addresses the same job.
_JOB_NAMESPACE = UUID("2c9d6f4e-5a0b-4b57-8e57-0e6f1b3a7d01")


@dataclass(frozen=True, eq=False)
class JobId(ValueObject):
    value: UUID

    @classmethod
    def from_source(cls, bot_id: int, chat_id: int, message_id: int) -> "JobId":
        return cls(uuid5(_JOB_NAMESPACE, f"{bot_id}:{chat_id}:{message_id}"))

    def _equality_components(self) -> tuple[Any, ...]:
        return (self.value,)
