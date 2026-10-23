from datetime import datetime
from uuid import UUID

from kernel_domain.entity import Entity


class AuthSession(Entity[UUID]):
    """A login: usable until it expires or is revoked."""

    def __init__(
        self,
        id: UUID,
        *,
        member_id: UUID,
        created_at: datetime,
        expires_at: datetime,
        revoked_at: datetime | None = None,
    ) -> None:
        super().__init__(id)
        self.member_id = member_id
        self.created_at = created_at
        self.expires_at = expires_at
        self.revoked_at = revoked_at

    def is_usable(self, member_id: UUID, now: datetime) -> bool:
        return (
            self.member_id == member_id
            and self.revoked_at is None
            and self.expires_at > now
        )
