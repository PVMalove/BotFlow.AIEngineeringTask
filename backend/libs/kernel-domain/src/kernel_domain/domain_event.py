import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    """Something that happened to an aggregate; independent of any broker.

    The event owns its `id`. A subclass adds its own fields and overrides
    `aggregate_id()` and `to_payload()`, which let a unit of work store the event
    without knowing the subclass.
    """

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    occurred_on_utc: datetime = field(default_factory=lambda: datetime.now(UTC))

    event_type: str = ""
    aggregate_type: str = ""

    def aggregate_id(self) -> uuid.UUID:
        """The id of the aggregate that raised the event."""
        raise NotImplementedError

    def to_payload(self) -> dict[str, Any]:
        """JSON-compatible fields of the event."""
        raise NotImplementedError
