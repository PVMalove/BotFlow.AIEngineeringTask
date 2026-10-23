from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from kernel_domain.value_object import ValueObject

_RESULT_INTENT_NAMESPACE = UUID("2c9d6f4e-5a0b-4b57-8e57-0e6f1b3a7d03")


@dataclass(frozen=True, eq=False)
class IntentId(ValueObject):
    value: UUID

    @classmethod
    def for_result(cls, job_id: UUID) -> "IntentId":
        """One result notification per job, whichever message reports the result."""
        return cls(uuid5(_RESULT_INTENT_NAMESPACE, str(job_id)))

    def _equality_components(self) -> tuple[Any, ...]:
        return (self.value,)
