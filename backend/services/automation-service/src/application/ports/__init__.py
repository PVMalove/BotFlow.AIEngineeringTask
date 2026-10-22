"""Boundaries used by the Telegram login command."""

from dataclasses import dataclass
from typing import Protocol


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


class Messenger(Protocol):
    async def send(self, chat_id: int, text: str) -> None: ...


class UpdateInbox(Protocol):
    async def claim(self, update_id: int) -> bool: ...
