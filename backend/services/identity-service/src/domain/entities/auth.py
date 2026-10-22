"""Members and their access state; no framework or storage imports."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class Role(StrEnum):
    ADMIN = "admin"
    USER = "user"


class MemberStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class Member:
    id: UUID
    telegram_id: int
    role: Role
    status: MemberStatus
    created_at: datetime

    def with_status(self, status: MemberStatus) -> "Member":
        return replace(self, status=status)
