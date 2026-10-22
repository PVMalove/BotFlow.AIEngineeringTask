"""Ports of the application layer."""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from domain.unit_of_work import UnitOfWork

Clock = Callable[[], datetime]
UnitOfWorkFactory = Callable[[], UnitOfWork]


class RateLimiter(Protocol):
    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        """Count one attempt; False once `limit` is exceeded in the window.

        The attempt stays counted even if the caller's own transaction rolls back.
        """
        ...
