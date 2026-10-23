from datetime import datetime
from uuid import UUID

from kernel_domain.entity import Entity
from kernel_domain.result import Result

from domain.errors import IdentityErrors
from domain.events.member_domain_event import (
    MemberApproved,
    MemberBlocked,
    MemberEvent,
    MemberRejected,
    MemberUnblocked,
)
from domain.value_objects.access import Action, JoinOutcome
from domain.value_objects.member_id import MemberId
from domain.value_objects.member_status import MemberStatus
from domain.value_objects.role import Role

# status a decision leads to; a member already there makes the repeat a no-op
_RESULT = {
    Action.APPROVE: MemberStatus.APPROVED,
    Action.REJECT: MemberStatus.REJECTED,
    Action.BLOCK: MemberStatus.BLOCKED,
    Action.UNBLOCK: MemberStatus.APPROVED,
}
# rejected -> pending happens only through a join; blocked is left only by unblock
_ALLOWED_FROM = {
    Action.APPROVE: {MemberStatus.PENDING},
    Action.REJECT: {MemberStatus.PENDING},
    Action.BLOCK: {MemberStatus.PENDING, MemberStatus.REJECTED, MemberStatus.APPROVED},
    Action.UNBLOCK: {MemberStatus.BLOCKED},
}
_EVENT = {
    Action.APPROVE: MemberApproved,
    Action.REJECT: MemberRejected,
    Action.BLOCK: MemberBlocked,
    Action.UNBLOCK: MemberUnblocked,
}


class Member(Entity[MemberId]):
    """A person known by Telegram identity and the access decisions about them.

    `access_version` grows with every access change; stale decisions and
    notifications compare it.
    """

    def __init__(
        self,
        id: MemberId,
        *,
        telegram_id: int,
        role: Role,
        status: MemberStatus,
        created_at: datetime,
        display_name: str | None = None,
        access_version: int = 1,
        status_changed_at: datetime | None = None,
        decided_by: UUID | None = None,
    ) -> None:
        super().__init__(id)
        self.telegram_id = telegram_id
        self.role = role
        self.status = status
        self.created_at = created_at
        self.display_name = display_name
        self.access_version = access_version
        self.status_changed_at = status_changed_at
        self.decided_by = decided_by

    @classmethod
    def request_join(
        cls, id: MemberId, *, telegram_id: int, display_name: str | None, now: datetime
    ) -> "Member":
        """A new pending member: the request itself."""
        return cls(
            id,
            telegram_id=telegram_id,
            role=Role.USER,
            status=MemberStatus.PENDING,
            created_at=now,
            display_name=display_name,
            status_changed_at=now,
        )

    @classmethod
    def initial_admin(
        cls, id: MemberId, *, telegram_id: int, now: datetime
    ) -> "Member":
        return cls(
            id,
            telegram_id=telegram_id,
            role=Role.ADMIN,
            status=MemberStatus.APPROVED,
            created_at=now,
        )

    @classmethod
    def reconstitute(
        cls,
        id: MemberId,
        *,
        telegram_id: int,
        role: Role,
        status: MemberStatus,
        created_at: datetime,
        display_name: str | None,
        access_version: int,
        status_changed_at: datetime | None,
        decided_by: UUID | None,
    ) -> "Member":
        return cls(
            id,
            telegram_id=telegram_id,
            role=role,
            status=status,
            created_at=created_at,
            display_name=display_name,
            access_version=access_version,
            status_changed_at=status_changed_at,
            decided_by=decided_by,
        )

    def join_again(self, display_name: str | None, now: datetime) -> JoinOutcome:
        """A repeated request: a rejected member is reopened, nobody else changes."""
        if self.status is MemberStatus.REJECTED:
            self.status = MemberStatus.PENDING
            self.display_name = display_name or self.display_name
            self.access_version += 1
            self.status_changed_at = now
            self.decided_by = None
            return JoinOutcome.SUBMITTED
        return {
            MemberStatus.PENDING: JoinOutcome.ALREADY_PENDING,
            MemberStatus.APPROVED: JoinOutcome.ALREADY_MEMBER,
            MemberStatus.BLOCKED: JoinOutcome.BLOCKED,
        }[self.status]

    def decide(
        self,
        action: Action,
        *,
        actor_id: UUID,
        approved_admins: int,
        expected_version: int,
        now: datetime,
    ) -> Result[bool]:
        """Apply an admin's decision; `approved_admins` counts inside the decision
        lock. `ok(False)` is a repeat of the reached decision: no effect, no event."""
        result = _RESULT[action]
        if self.status is result:
            return Result[bool].ok(False)  # a repeat has no new effect
        if self.access_version != expected_version:
            return Result[bool].fail(IdentityErrors.version_conflict())
        if self.status not in _ALLOWED_FROM[action]:
            return Result[bool].fail(IdentityErrors.invalid_transition())
        if action is Action.BLOCK:
            if self.id.value == actor_id:
                return Result[bool].fail(IdentityErrors.cannot_block_self())
            if (
                self.role is Role.ADMIN
                and self.status is MemberStatus.APPROVED
                and approved_admins <= 1
            ):
                return Result[bool].fail(IdentityErrors.last_admin())
        self.status = result
        self.access_version += 1
        self.status_changed_at = now
        self.decided_by = actor_id
        self.add_domain_event(self._event(action, actor_id, now))
        return Result[bool].ok(True)

    def _event(self, action: Action, actor_id: UUID, now: datetime) -> MemberEvent:
        return _EVENT[action](
            member_id=self.id,
            telegram_id=self.telegram_id,
            access_version=self.access_version,
            decided_by=actor_id,
            occurred_on_utc=now,
        )
