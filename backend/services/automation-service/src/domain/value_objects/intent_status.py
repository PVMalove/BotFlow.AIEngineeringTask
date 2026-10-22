from enum import StrEnum


class IntentStatus(StrEnum):
    PENDING = "pending"
    ENQUEUED = "enqueued"
    SENT = "sent"
    SUPPRESSED_STALE = "suppressed_stale"
    SUPPRESSED_BLOCKED = "suppressed_blocked"
    FAILED = "failed"


class Verdict(StrEnum):
    SEND = "send"
    STALE = "stale"
    BLOCKED = "blocked"
