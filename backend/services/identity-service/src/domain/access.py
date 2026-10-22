"""Join requests and access decisions; no framework or storage imports."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from domain.entities.auth import Member, MemberStatus, Role


class JoinOutcome(StrEnum):
    SUBMITTED = "submitted"
    ALREADY_PENDING = "already_pending"
    ALREADY_MEMBER = "already_member"
    BLOCKED = "blocked"


class Action(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    BLOCK = "block"
    UNBLOCK = "unblock"


class AccessError(Exception):
    """Base class of expected access-decision failures."""


class MemberNotFound(AccessError): ...


class AdminRequired(AccessError):
    """The actor's current role is not admin."""


class VersionConflict(AccessError):
    """The member changed since the admin looked at it."""


class InvalidTransition(AccessError): ...


class CannotBlockSelf(AccessError): ...


class LastAdmin(AccessError):
    """Blocking would leave no approved admin."""


# status a decision leads to; a member already there makes the repeat a no-op
RESULT = {
    Action.APPROVE: MemberStatus.APPROVED,
    Action.REJECT: MemberStatus.REJECTED,
    Action.BLOCK: MemberStatus.BLOCKED,
    Action.UNBLOCK: MemberStatus.APPROVED,
}
# rejected -> pending happens only through /join; blocked is left only by unblock
ALLOWED_FROM = {
    Action.APPROVE: {MemberStatus.PENDING},
    Action.REJECT: {MemberStatus.PENDING},
    Action.BLOCK: {MemberStatus.PENDING, MemberStatus.REJECTED, MemberStatus.APPROVED},
    Action.UNBLOCK: {MemberStatus.BLOCKED},
}


@dataclass(frozen=True, slots=True)
class Decision:
    member: Member
    changed: bool


def decide(
    target: Member,
    action: Action,
    *,
    actor_id: UUID,
    approved_admins: int,
    expected_version: int,
    now: datetime,
) -> Decision:
    """Pure decision table; `approved_admins` counts inside the decision lock."""
    result = RESULT[action]
    if target.status is result:
        return Decision(target, changed=False)  # a repeat has no new effect
    if target.access_version != expected_version:
        raise VersionConflict
    if target.status not in ALLOWED_FROM[action]:
        raise InvalidTransition
    if action is Action.BLOCK:
        if target.id == actor_id:
            raise CannotBlockSelf
        if (
            target.role is Role.ADMIN
            and target.status is MemberStatus.APPROVED
            and approved_admins <= 1
        ):
            raise LastAdmin
    return Decision(
        replace(
            target,
            status=result,
            access_version=target.access_version + 1,
            status_changed_at=now,
            decided_by=actor_id,
        ),
        changed=True,
    )
