from datetime import datetime
from uuid import UUID

from kernel_domain.entity import Entity


class LoginCode(Entity[UUID]):
    """Only the hash of the code is kept; the code itself is shown once."""

    def __init__(
        self,
        id: UUID,
        *,
        member_id: UUID,
        code_hash: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        super().__init__(id)
        self.member_id = member_id
        self.code_hash = code_hash
        self.created_at = created_at
        self.expires_at = expires_at
