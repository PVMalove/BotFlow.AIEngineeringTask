from uuid import UUID

from kernel_domain.entity import Entity
from kernel_domain.result import Result

from domain.errors import AutomationErrors
from domain.value_objects.intent_id import IntentId
from domain.value_objects.intent_status import IntentStatus, Verdict

# Decisions are valid only for the access version they were made at.
DECISION_KINDS = frozenset({"access.approved", "access.rejected", "access.unblocked"})
# Final notifications of accepted work must go through the same rule.
RESULT_KINDS = frozenset({"task.result", "transcription.result"})
_OPEN = (IntentStatus.PENDING, IntentStatus.ENQUEUED)


class NotificationIntent(Entity[IntentId]):
    """A message to a person in Telegram that may still be sent, and its progress."""

    def __init__(
        self,
        id: IntentId,
        *,
        chat_id: int,  # a private chat id equals the Telegram user id
        member_id: UUID,
        kind: str,
        access_version: int,
        status: IntentStatus = IntentStatus.PENDING,
        attempts: int = 0,
        body: str | None = None,  # the text of a result; decisions use fixed texts
    ) -> None:
        super().__init__(id)
        self.chat_id = chat_id
        self.member_id = member_id
        self.kind = kind
        self.access_version = access_version
        self.status = status
        self.attempts = attempts
        self.body = body

    @classmethod
    def access_decision(
        cls,
        id: IntentId,
        *,
        chat_id: int,
        member_id: UUID,
        decision: str,
        access_version: int,
    ) -> "NotificationIntent":
        return cls(
            id,
            chat_id=chat_id,
            member_id=member_id,
            kind=f"access.{decision}",
            access_version=access_version,
        )

    @classmethod
    def result(
        cls,
        id: IntentId,
        *,
        chat_id: int,
        member_id: UUID,
        kind: str,
        body: str,
    ) -> "NotificationIntent":
        """A result is not tied to an access decision (version 0)."""
        return cls(
            id,
            chat_id=chat_id,
            member_id=member_id,
            kind=kind,
            access_version=0,
            body=body,
        )

    @classmethod
    def reconstitute(
        cls,
        id: IntentId,
        *,
        chat_id: int,
        member_id: UUID,
        kind: str,
        access_version: int,
        status: IntentStatus,
        attempts: int,
        body: str | None,
    ) -> "NotificationIntent":
        return cls(
            id,
            chat_id=chat_id,
            member_id=member_id,
            kind=kind,
            access_version=access_version,
            status=status,
            attempts=attempts,
            body=body,
        )

    @property
    def is_open(self) -> bool:
        return self.status in _OPEN

    def delivery_verdict(
        self, member_status: str | None, member_version: int
    ) -> Result[Verdict]:
        """`member_status` is None when Identity no longer knows the member."""
        if self.kind in DECISION_KINDS:
            known = member_status is not None
            fresh = known and member_version == self.access_version
            return Result[Verdict].ok(Verdict.SEND if fresh else Verdict.STALE)
        if self.kind in RESULT_KINDS:
            # Accepted work keeps running; only its final notification to a
            # blocked (or departed) author is suppressed.
            approved = member_status == "approved"
            return Result[Verdict].ok(Verdict.SEND if approved else Verdict.BLOCKED)
        return Result[Verdict].fail(AutomationErrors.unknown_notification_kind())

    def mark_enqueued(self) -> bool:
        if self.status is not IntentStatus.PENDING:
            return False
        self.status = IntentStatus.ENQUEUED
        return True

    def settle(self, status: IntentStatus) -> bool:
        """End the delivery; only an open intent can be settled."""
        if not self.is_open:
            return False
        self.status = status
        return True

    def record_failure(self, max_attempts: int) -> IntentStatus:
        """Count a failed delivery; FAILED once `max_attempts` is reached."""
        if not self.is_open:
            return IntentStatus.FAILED
        self.attempts += 1
        if self.attempts >= max_attempts:
            self.status = IntentStatus.FAILED
        return self.status
