from enum import StrEnum


class JoinOutcome(StrEnum):
    """Identity's answer to a join request; Automation never decides it."""

    SUBMITTED = "submitted"
    ALREADY_PENDING = "already_pending"
    ALREADY_MEMBER = "already_member"
    BLOCKED = "blocked"
