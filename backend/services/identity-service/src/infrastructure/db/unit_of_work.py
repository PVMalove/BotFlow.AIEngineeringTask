"""SQLAlchemy unit of work: one session per operation, rollback by default."""

from types import TracebackType
from typing import Any, Self

from sqlalchemy import Row, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.entities.auth import Member, MemberStatus, Role
from infrastructure.db.tables import members


class SqlMembers:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_if_absent(self, member: Member) -> bool:
        result = await self._session.execute(
            insert(members)
            .values(
                id=member.id,
                telegram_id=member.telegram_id,
                role=member.role.value,
                status=member.status.value,
                created_at=member.created_at,
            )
            .on_conflict_do_nothing(index_elements=[members.c.telegram_id])
            .returning(members.c.id)
        )
        return result.scalar_one_or_none() is not None

    async def get_by_telegram_id(
        self, telegram_id: int, *, lock: bool = False
    ) -> Member | None:
        query = select(members).where(members.c.telegram_id == telegram_id)
        if lock:
            query = query.with_for_update()
        row = (await self._session.execute(query)).one_or_none()
        return _member(row) if row else None


def _member(row: Row[Any]) -> Member:
    return Member(
        id=row.id,
        telegram_id=row.telegram_id,
        role=Role(row.role),
        status=MemberStatus(row.status),
        created_at=row.created_at,
    )


class SqlUnitOfWork:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> Self:
        self._session = self._sessions()
        self.members = SqlMembers(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        assert self._session is not None
        await self._session.close()  # discards anything not committed

    async def commit(self) -> None:
        assert self._session is not None
        await self._session.commit()
