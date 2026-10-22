"""Join requests and access decisions; no framework or storage imports."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from kernel_domain.result import Result

from domain.entities.auth import Member, MemberStatus, Role
from domain.errors import IdentityErrors


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
) -> Result[Decision]:
    """Pure decision table; `approved_admins` counts inside the decision lock."""
    result = RESULT[action]
    if target.status is result:
        # a repeat has no new effect
        return Result[Decision].ok(Decision(target, changed=False))
    if target.access_version != expected_version:
        return Result[Decision].fail(IdentityErrors.version_conflict())
    if target.status not in ALLOWED_FROM[action]:
        return Result[Decision].fail(IdentityErrors.invalid_transition())
    if action is Action.BLOCK:
        if target.id == actor_id:
            return Result[Decision].fail(IdentityErrors.cannot_block_self())
        if (
            target.role is Role.ADMIN
            and target.status is MemberStatus.APPROVED
            and approved_admins <= 1
        ):
            return Result[Decision].fail(IdentityErrors.last_admin())
    return Result[Decision].ok(
        Decision(
            replace(
                target,
                status=result,
                access_version=target.access_version + 1,
                status_changed_at=now,
                decided_by=actor_id,
            ),
            changed=True,
        )
    )


ACCESS_DECIDED = "member.access-decided"
# A block deliberately has no notification: the blocked author hears nothing more.
_NOTIFIED_AS = {
    Action.APPROVE: "approved",
    Action.REJECT: "rejected",
    Action.UNBLOCK: "unblocked",
}


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    """An event stored in the decision transaction and relayed afterwards."""

    id: UUID
    type: str
    payload: dict[str, Any]
    created_at: datetime
    attempts: int = 0
    next_attempt_at: datetime | None = None
    published_at: datetime | None = None


def access_decided(member: Member, action: Action, now: datetime) -> OutboxEntry | None:
    """The notification intent of a decision that just changed `member`."""
    decision = _NOTIFIED_AS.get(action)
    if decision is None:
        return None
    return OutboxEntry(
        id=uuid4(),
        type=ACCESS_DECIDED,
        payload={
            "member_id": str(member.id),
            "telegram_id": member.telegram_id,
            "decision": decision,
            "access_version": member.access_version,
            "decided_at": now.isoformat(),
        },
        created_at=now,
        next_attempt_at=now,
    )
