"""Framework-independent views."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from domain.entities.member import Member


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

    @classmethod
    def from_domain(cls, member: Member) -> "MemberView":
        return cls(
            id=member.id.value,
            telegram_id=member.telegram_id,
            role=member.role.value,
            status=member.status.value,
            access_version=member.access_version,
        )


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

    @classmethod
    def from_domain(cls, member: Member) -> "AdminMemberView":
        return cls(
            id=member.id.value,
            telegram_id=member.telegram_id,
            display_name=member.display_name,
            role=member.role.value,
            status=member.status.value,
            access_version=member.access_version,
            created_at=member.created_at,
            status_changed_at=member.status_changed_at,
        )


@dataclass(frozen=True, slots=True)
class DecisionView:
    member: AdminMemberView
    changed: bool


@dataclass(frozen=True, slots=True)
class JoinRequestView:
    outcome: str
    member: MemberView
