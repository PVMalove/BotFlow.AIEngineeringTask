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
    display_name: str | None = None
    # Bumped by every access change; stale decisions and notifications compare it.
    access_version: int = 1
    status_changed_at: datetime | None = None
    decided_by: UUID | None = None

    def with_status(self, status: MemberStatus) -> "Member":
        return replace(self, status=status)


@dataclass(frozen=True, slots=True)
class LoginCode:
    """Only the hash of the code is kept; the code itself is shown once."""

    id: UUID
    member_id: UUID
    code_hash: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AuthSession:
    id: UUID
    member_id: UUID
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
