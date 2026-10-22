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


class AuthError(Exception):
    """Base class of expected authentication failures."""


class InvalidLoginCode(AuthError):
    """Unknown, malformed, expired, cancelled or already used code."""


class InvalidSession(AuthError):
    """Missing, forged, expired or revoked session, or a member who lost access."""


class MemberNotApproved(AuthError):
    """The Telegram identity has no approved membership."""


class RateLimited(AuthError):
    """Too many attempts in the current window."""


@dataclass(frozen=True, slots=True)
class Member:
    id: UUID
    telegram_id: int
    role: Role
    status: MemberStatus
    created_at: datetime

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
