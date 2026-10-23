"""One-time login codes in PostgreSQL, used in the unit of work's session."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from domain.entities.login_code import LoginCode
from infrastructure.db.models import LoginCodeModel


class SqlLoginCodes:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def cancel_active(self, member_id: UUID, now: datetime) -> None:
        await self._session.execute(
            update(LoginCodeModel)
            .where(
                LoginCodeModel.member_id == member_id,
                LoginCodeModel.consumed_at.is_(None),
                LoginCodeModel.cancelled_at.is_(None),
            )
            .values(cancelled_at=now)
        )

    async def add(self, code: LoginCode) -> None:
        self._session.add(
            LoginCodeModel(
                id=code.id,
                member_id=code.member_id,
                code_hash=code.code_hash,
                created_at=code.created_at,
                expires_at=code.expires_at,
            )
        )
        await self._session.flush()

    async def consume(self, code_hash: str, now: datetime) -> UUID | None:
        # One statement: of two concurrent exchanges only one matches the row.
        result = await self._session.execute(
            update(LoginCodeModel)
            .where(
                LoginCodeModel.code_hash == code_hash,
                LoginCodeModel.consumed_at.is_(None),
                LoginCodeModel.cancelled_at.is_(None),
                LoginCodeModel.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(LoginCodeModel.member_id)
        )
        member_id: UUID | None = result.scalar_one_or_none()
        return member_id
