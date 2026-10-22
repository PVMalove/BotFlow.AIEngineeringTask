"""Domain repository ports; repositories never commit."""

from typing import Protocol

from domain.entities.auth import Member


class MemberRepository(Protocol):
    async def add_if_absent(self, member: Member) -> bool:
        """Insert unless the Telegram identity exists; True when inserted."""
        ...

    async def get_by_telegram_id(
        self, telegram_id: int, *, lock: bool = False
    ) -> Member | None: ...
