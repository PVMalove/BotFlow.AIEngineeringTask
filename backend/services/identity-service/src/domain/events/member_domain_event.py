from dataclasses import dataclass
from typing import Any
from uuid import UUID

from kernel_domain.domain_event import DomainEvent

from domain.value_objects.member_id import MemberId


@dataclass(frozen=True, kw_only=True)
class MemberEvent(DomainEvent):
    """Common ancestor of the member events: they all name their member."""

    aggregate_type: str = "Member"

    member_id: MemberId
    telegram_id: int
    access_version: int
    decided_by: UUID

    def aggregate_id(self) -> UUID:
        return self.member_id.value

    def to_payload(self) -> dict[str, Any]:
        return {
            "member_id": str(self.member_id.value),
            "telegram_id": self.telegram_id,
            "access_version": self.access_version,
        }


@dataclass(frozen=True, kw_only=True)
class MemberApproved(MemberEvent):
    event_type: str = "member.approved"


@dataclass(frozen=True, kw_only=True)
class MemberRejected(MemberEvent):
    event_type: str = "member.rejected"


@dataclass(frozen=True, kw_only=True)
class MemberBlocked(MemberEvent):
    """A block has no notification: the blocked author hears nothing more."""

    event_type: str = "member.blocked"


@dataclass(frozen=True, kw_only=True)
class MemberUnblocked(MemberEvent):
    event_type: str = "member.unblocked"
