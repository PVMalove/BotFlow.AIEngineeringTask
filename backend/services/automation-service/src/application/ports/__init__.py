"""Boundaries used by the Telegram login and join commands."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


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
