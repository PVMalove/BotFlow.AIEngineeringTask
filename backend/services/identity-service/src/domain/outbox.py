"""An event stored in the transaction of its change and relayed to the broker later."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

ACCESS_DECIDED = "member.access-decided"


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    id: UUID
    type: str
    payload: dict[str, Any]
    created_at: datetime
    attempts: int = 0
    next_attempt_at: datetime | None = None
    published_at: datetime | None = None
