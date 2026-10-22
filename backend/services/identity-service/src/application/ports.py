"""Ports of the application layer."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from kernel_domain.result import Result

from domain.entities.auth_session import AuthSession
from domain.outbox import OutboxEntry
from domain.unit_of_work import IdentityUnitOfWork

Clock = Callable[[], datetime]
UnitOfWorkFactory = Callable[[], IdentityUnitOfWork]


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

    def verify(self, token: str) -> Result[TokenClaims]:
        """Check signature, issuer, audience and lifetime; else an invalid session."""
        ...


class EventPublisher(Protocol):
    async def publish(self, entry: OutboxEntry) -> None:
        """Return only once the broker has confirmed; raise on any failure."""
        ...
