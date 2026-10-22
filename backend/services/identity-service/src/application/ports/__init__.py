"""Ports of the application layer."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from domain.entities.auth import AuthSession
from domain.unit_of_work import UnitOfWork

Clock = Callable[[], datetime]
UnitOfWorkFactory = Callable[[], UnitOfWork]


class RateLimiter(Protocol):
    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        """Count one attempt; False once `limit` is exceeded in the window.

        The attempt stays counted even if the caller's own transaction rolls back.
        """
        ...


@dataclass(frozen=True, slots=True)
class TokenClaims:
    session_id: UUID
    member_id: UUID


class TokenService(Protocol):
    def issue(self, session: AuthSession) -> str: ...

    def verify(self, token: str) -> TokenClaims:
        """Check signature, issuer, audience and lifetime; else InvalidSession."""
        ...
