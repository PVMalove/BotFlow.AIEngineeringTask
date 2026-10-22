"""Notification intents and the rule deciding whether one may still be sent."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class IntentStatus(StrEnum):
    PENDING = "pending"
    ENQUEUED = "enqueued"
    SENT = "sent"
    SUPPRESSED_STALE = "suppressed_stale"
    SUPPRESSED_BLOCKED = "suppressed_blocked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class NotificationIntent:
    id: UUID
    chat_id: int  # a private chat id equals the Telegram user id
    member_id: UUID
    kind: str
    access_version: int
    status: IntentStatus = IntentStatus.PENDING
    attempts: int = 0
    body: str | None = None  # the text of a result; decisions use fixed texts


class Verdict(StrEnum):
    SEND = "send"
    STALE = "stale"
    BLOCKED = "blocked"


class UnknownNotificationKind(Exception):
    """No delivery rule exists for this kind; it is never sent by default."""


# Decisions are valid only for the access version they were made at.
DECISION_KINDS = frozenset({"access.approved", "access.rejected", "access.unblocked"})
# Final notifications of accepted work (later slices) must go through the same rule.
RESULT_KINDS = frozenset({"task.result", "transcription.result"})


def delivery_verdict(
    kind: str,
    intent_access_version: int,
    member_status: str | None,
    member_version: int,
) -> Verdict:
    """`member_status` is None when Identity no longer knows the member."""
    if kind in DECISION_KINDS:
        known = member_status is not None
        return (
            Verdict.SEND
            if known and member_version == intent_access_version
            else Verdict.STALE
        )
    if kind in RESULT_KINDS:
        # Accepted work keeps running; only its final notification to a
        # blocked (or departed) author is suppressed.
        return Verdict.SEND if member_status == "approved" else Verdict.BLOCKED
    raise UnknownNotificationKind(kind)
