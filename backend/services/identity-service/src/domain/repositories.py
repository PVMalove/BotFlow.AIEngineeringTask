"""Domain repository ports; repositories never commit."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from domain.entities.auth import AuthSession, LoginCode, Member


class MemberRepository(Protocol):
    async def add_if_absent(self, member: Member) -> bool:
        """Insert unless the Telegram identity exists; True when inserted."""
        ...

    async def get(self, member_id: UUID) -> Member | None: ...

    async def get_by_telegram_id(
        self, telegram_id: int, *, lock: bool = False
    ) -> Member | None:
        """`lock` serializes concurrent changes to the same member."""
        ...


class LoginCodeRepository(Protocol):
    async def cancel_active(self, member_id: UUID, now: datetime) -> None: ...

    async def add(self, code: LoginCode) -> None: ...

    async def consume(self, code_hash: str, now: datetime) -> UUID | None:
        """Atomically use a live code once; return its member or None."""
        ...


class SessionRepository(Protocol):
    async def add(self, session: AuthSession) -> None: ...
