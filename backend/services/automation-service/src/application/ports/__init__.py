"""Boundaries used by the Telegram login and join commands."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from domain.notifications import IntentStatus, NotificationIntent


class JoinOutcome(StrEnum):
    """Identity's answer to a join request; Automation never decides it."""

    SUBMITTED = "submitted"
    ALREADY_PENDING = "already_pending"
    ALREADY_MEMBER = "already_member"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class TelegramMember:
    telegram_id: int
    role: str
    status: str
    access_version: int = 0
    member_id: UUID | None = None  # Identity's stable member id


class IdentityUnavailable(Exception):
    """The identity service cannot complete the operation."""


class LoginDenied(Exception):
    """The member is not approved."""


class LoginRateLimited(Exception):
    """The caller must wait before requesting another code."""


class MemberDirectory(Protocol):
    async def find_member(self, telegram_id: int) -> TelegramMember | None: ...


class IdentityGateway(Protocol):
    async def find_member(self, telegram_id: int) -> TelegramMember | None: ...

    async def issue_login_code(self, telegram_id: int) -> str: ...

    async def submit_join_request(
        self, telegram_id: int, display_name: str | None
    ) -> JoinOutcome: ...


class Messenger(Protocol):
    async def send(self, chat_id: int, text: str) -> None: ...


class UpdateInbox(Protocol):
    async def claim(self, update_id: int) -> bool: ...


class IntentStore(Protocol):
    async def store_event(self, message_id: str, intent: NotificationIntent) -> bool:
        """Inbox record and intent in ONE transaction; False for a redelivery."""
        ...

    async def pending(self, limit: int) -> list[UUID]: ...

    async def mark_enqueued(self, intent_id: UUID) -> None: ...

    async def get(self, intent_id: UUID) -> NotificationIntent | None: ...

    async def settle(self, intent_id: UUID, status: IntentStatus) -> None: ...

    async def record_failure(self, intent_id: UUID, max_attempts: int) -> IntentStatus:
        """Count a failed delivery; FAILED once `max_attempts` is reached."""
        ...


@dataclass(frozen=True, slots=True)
class QueuedIntent:
    entry_id: str
    intent_id: UUID | None  # None for an entry that names no intent


class NotificationQueue(Protocol):
    async def add(self, intent_id: UUID) -> None: ...

    async def claim_idle(self, count: int) -> list[QueuedIntent]: ...

    async def read(self, count: int, block_ms: int) -> list[QueuedIntent]: ...

    async def ack(self, entry_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class NewJob:
    id: UUID
    bot_id: int
    chat_id: int
    message_id: int
    author_id: UUID
    author_telegram_id: int
    author_name: str  # snapshot from Telegram, at most 64 characters
    text: str
    message_date: datetime  # when Telegram says the author sent it
    received_at: datetime  # when this process received the update


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    """A message written in the business transaction and relayed to RabbitMQ."""

    id: UUID
    correlation_id: UUID
    type: str
    routing_key: str
    payload: dict[str, Any]  # the complete envelope


class AcceptOutcome(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"  # the same bot/chat/message was already accepted
    LIMIT = "limit"  # too many unfinished jobs; nothing was stored


@dataclass(frozen=True, slots=True)
class Accepted:
    outcome: AcceptOutcome
    job_id: UUID
    acknowledged: bool  # a positive ACK was already recorded for this job


class JobStore(Protocol):
    async def accept(self, job: NewJob, message: OutboxMessage) -> Accepted:
        """Duplicate check, limits, job and outbox row in ONE transaction."""
        ...

    async def mark_acked(self, job_id: UUID, at: datetime) -> None: ...


@dataclass(frozen=True, slots=True)
class JobSummary:
    id: UUID
    chat_id: int
    author_id: UUID
    text: str


class ResultOutcome(StrEnum):
    RECORDED = "recorded"
    DUPLICATE = "duplicate"  # this result was already recorded
    UNKNOWN_JOB = "unknown_job"  # no such job: the message can never work


class TaskResultStore(Protocol):
    async def record(
        self,
        message_id: str,
        job_id: UUID,
        task_id: UUID,
        intent_for: Callable[[JobSummary], NotificationIntent],
    ) -> ResultOutcome:
        """Inbox, job completion and intent in ONE transaction.

        A job that failed after its retries completes as well: a late result wins.
        """
        ...
