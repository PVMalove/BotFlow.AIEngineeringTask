"""Boundaries used by the Telegram login and join commands."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
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


class IdentityUnavailable(Exception):
    """The identity service cannot complete the operation."""


class LoginDenied(Exception):
    """The member is not approved."""


class LoginRateLimited(Exception):
    """The caller must wait before requesting another code."""


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
