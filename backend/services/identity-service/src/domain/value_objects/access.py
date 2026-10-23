from enum import StrEnum


class Action(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    BLOCK = "block"
    UNBLOCK = "unblock"


class JoinOutcome(StrEnum):
    SUBMITTED = "submitted"
    ALREADY_PENDING = "already_pending"
    ALREADY_MEMBER = "already_member"
    BLOCKED = "blocked"
