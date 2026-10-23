"""Web auth sessions in PostgreSQL, used in the unit of work's session."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, update
from sqlalchemy.ext.asyncio import AsyncSession

from domain.entities.auth_session import AuthSession
from infrastructure.db.models import AuthSessionModel


class SqlSessions:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, session: AuthSession) -> None:
        self._session.add(
            AuthSessionModel(
                id=session.id,
                member_id=session.member_id,
                created_at=session.created_at,
                expires_at=session.expires_at,
            )
        )
        await self._session.flush()

    async def get(self, session_id: UUID) -> AuthSession | None:
        row = await self._session.get(AuthSessionModel, session_id)
        if row is None:
            return None
        return AuthSession(
            row.id,
            member_id=row.member_id,
            created_at=row.created_at,
            expires_at=row.expires_at,
            revoked_at=row.revoked_at,
        )

    async def revoke(self, session_id: UUID, now: datetime) -> None:
        await self._revoke(AuthSessionModel.id == session_id, now)

    async def revoke_all(self, member_id: UUID, now: datetime) -> None:
        await self._revoke(AuthSessionModel.member_id == member_id, now)

    async def _revoke(self, selector: ColumnElement[bool], now: datetime) -> None:
        await self._session.execute(
            update(AuthSessionModel)
            .where(selector, AuthSessionModel.revoked_at.is_(None))
            .values(revoked_at=now)
        )
