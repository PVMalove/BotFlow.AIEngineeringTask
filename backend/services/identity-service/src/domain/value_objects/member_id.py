from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from kernel_domain.value_object import ValueObject


@dataclass(frozen=True, eq=False)
class MemberId(ValueObject):
    value: UUID

    @classmethod
    def new_id(cls) -> "MemberId":
        return cls(uuid4())

    def _equality_components(self) -> tuple[Any, ...]:
        return (self.value,)
