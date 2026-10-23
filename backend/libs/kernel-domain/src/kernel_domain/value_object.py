from abc import ABC, abstractmethod
from typing import Any


class ValueObject(ABC):
    """Immutable value compared by its components, not by identity.

    Objects of different types are never equal, even with equal components.
    """

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return False
        return self._equality_components() == other._equality_components()

    def __hash__(self) -> int:
        return hash((type(self), self._equality_components()))

    @abstractmethod
    def _equality_components(self) -> tuple[Any, ...]:
        """The values that make up the object, in a fixed order."""
        ...
