"""Framework-independent views."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class LoginCodeView:
    code: str
    expires_at: datetime
