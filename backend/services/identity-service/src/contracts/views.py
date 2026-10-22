"""Framework-independent views."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class LoginCodeView:
    code: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class MemberView:
    id: UUID
    telegram_id: int
    role: str
    status: str
