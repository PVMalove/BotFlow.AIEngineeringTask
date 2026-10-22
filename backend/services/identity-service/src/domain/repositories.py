"""Domain repository ports; repositories never commit."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from domain.entities.auth import AuthSession, LoginCode, Member, MemberStatus


class MemberRepository(Protocol):
    async def add_if_absent(self, member: Member) -> bool:
        """Insert unless the Telegram identity exists; True when inserted."""
        ...

    async def get(self, member_id: UUID, *, lock: bool = False) -> Member | None: ...

    async def get_by_telegram_id(
        self, telegram_id: int, *, lock: bool = False
    ) -> Member | None:
        """`lock` serializes concurrent changes to the same member."""
        ...

    async def get_by_login_code(
        self, code_hash: str, *, lock: bool = False
    ) -> Member | None:
        """The code's member, locked before the code row is touched."""
        ...

    async def lock_decisions(self) -> None:
        """Serialize access decisions until the transaction ends."""
        ...

    async def count_approved_admins(self) -> int: ...

    async def list_by_status(
        self, statuses: tuple[MemberStatus, ...], *, limit: int, offset: int
    ) -> list[Member]:
        """Ordered by created_at, then id, so pages are stable."""
        ...

    async def update_access(self, member: Member, *, expected_version: int) -> bool:
        """Store the access fields if the stored version is still `expected_version`."""
        ...


class LoginCodeRepository(Protocol):
    async def cancel_active(self, member_id: UUID, now: datetime) -> None: ...

    async def add(self, code: LoginCode) -> None: ...

    async def consume(self, code_hash: str, now: datetime) -> UUID | None:
        """Atomically use a live code once; return its member or None."""
        ...


class SessionRepository(Protocol):
    async def add(self, session: AuthSession) -> None: ...

    async def get(self, session_id: UUID) -> AuthSession | None: ...

    async def revoke(self, session_id: UUID, now: datetime) -> None: ...

    async def revoke_all(self, member_id: UUID, now: datetime) -> None: ...
