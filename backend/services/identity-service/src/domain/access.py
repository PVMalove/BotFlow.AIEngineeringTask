"""Join requests and access decisions; no framework or storage imports."""

from enum import StrEnum


class JoinOutcome(StrEnum):
    SUBMITTED = "submitted"
    ALREADY_PENDING = "already_pending"
    ALREADY_MEMBER = "already_member"
    BLOCKED = "blocked"
