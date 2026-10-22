"""Framework-independent views."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class LoginCodeView:
    code: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class MemberView:
    id: UUID
    telegram_id: int
    role: str
    status: str
    access_version: int


@dataclass(frozen=True, slots=True)
class AdminMemberView:
    id: UUID
    telegram_id: int
    display_name: str | None
    role: str
    status: str
    access_version: int
    created_at: datetime
    status_changed_at: datetime | None


@dataclass(frozen=True, slots=True)
class DecisionView:
    member: AdminMemberView
    changed: bool


@dataclass(frozen=True, slots=True)
class JoinRequestView:
    outcome: str
    member: MemberView
