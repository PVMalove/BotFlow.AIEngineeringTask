"""SQLAlchemy unit of work: one session per operation, rollback by default."""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from kernel_domain.entity import Entity
from kernel_platform.unit_of_work import EventHandler, SqlAlchemyUnitOfWork
from sqlalchemy import Row, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.entities.auth_session import AuthSession
from domain.entities.login_code import LoginCode
from domain.entities.member import Member
from domain.repositories import (
    LoginCodeRepository,
    MemberRepository,
    OutboxRepository,
    SessionRepository,
)
from domain.value_objects.member_id import MemberId
from domain.value_objects.member_status import MemberStatus
from domain.value_objects.role import Role
from infrastructure.db.outbox import SqlOutbox
from infrastructure.db.outbox_events import stage_identity_event
from infrastructure.db.tables import (
    auth_sessions,
    login_codes,
    members,
    rate_limits,
)

# Serializes access decisions; migrations use their own key (7_204_121).
_DECISION_LOCK_KEY = 7_204_122


class SqlMembers:
    def __init__(
        self, session: AsyncSession, track: Callable[[Entity[MemberId]], None]
    ) -> None:
        self._session = session
        self._track = track

    async def add_if_absent(self, member: Member) -> bool:
        result = await self._session.execute(
            insert(members)
            .values(
                id=member.id.value,
                telegram_id=member.telegram_id,
                role=member.role.value,
                status=member.status.value,
                created_at=member.created_at,
                display_name=member.display_name,
                access_version=member.access_version,
                status_changed_at=member.status_changed_at,
                decided_by=member.decided_by,
            )
            .on_conflict_do_nothing(index_elements=[members.c.telegram_id])
            .returning(members.c.id)
        )
        return result.scalar_one_or_none() is not None

    async def get(self, member_id: MemberId, *, lock: bool = False) -> Member | None:
        query = select(members).where(members.c.id == member_id.value)
        if lock:
            query = query.with_for_update()
        return self._loaded((await self._session.execute(query)).one_or_none())

    async def get_by_telegram_id(
        self, telegram_id: int, *, lock: bool = False
    ) -> Member | None:
        query = select(members).where(members.c.telegram_id == telegram_id)
        if lock:
            query = query.with_for_update()
        return self._loaded((await self._session.execute(query)).one_or_none())

    async def get_by_login_code(
        self, code_hash: str, *, lock: bool = False
    ) -> Member | None:
        query = (
            select(members)
            .join(login_codes, login_codes.c.member_id == members.c.id)
            .where(login_codes.c.code_hash == code_hash)
        )
        if lock:
            query = query.with_for_update(of=members)
        return self._loaded((await self._session.execute(query)).one_or_none())

    async def lock_decisions(self) -> None:
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": _DECISION_LOCK_KEY}
        )

    async def count_approved_admins(self) -> int:
        count = await self._session.scalar(
            select(func.count())
            .select_from(members)
            .where(
                members.c.role == Role.ADMIN.value,
                members.c.status == MemberStatus.APPROVED.value,
            )
        )
        return int(count or 0)

    async def list_by_status(
        self, statuses: tuple[MemberStatus, ...], *, limit: int, offset: int
    ) -> list[Member]:
        rows = await self._session.execute(
            select(members)
            .where(members.c.status.in_([status.value for status in statuses]))
            .order_by(members.c.created_at, members.c.id)
            .limit(limit)
            .offset(offset)
        )
        return [_member(row) for row in rows]

    async def update_access(self, member: Member, *, loaded_version: int) -> bool:
        result = await self._session.execute(
            update(members)
            .where(
                members.c.id == member.id.value,
                members.c.access_version == loaded_version,
            )
            .values(
                status=member.status.value,
                display_name=member.display_name,
                access_version=member.access_version,
                status_changed_at=member.status_changed_at,
                decided_by=member.decided_by,
            )
            .returning(members.c.id)
        )
        if result.scalar_one_or_none() is None:
            return False
        self._track(member)  # its events are staged by the commit
        return True

    def _loaded(self, row: Row[Any] | None) -> Member | None:
        return _member(row) if row else None


class SqlLoginCodes:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def cancel_active(self, member_id: UUID, now: datetime) -> None:
        await self._session.execute(
            update(login_codes)
            .where(
                login_codes.c.member_id == member_id,
                login_codes.c.consumed_at.is_(None),
                login_codes.c.cancelled_at.is_(None),
            )
            .values(cancelled_at=now)
        )

    async def add(self, code: LoginCode) -> None:
        await self._session.execute(
            login_codes.insert().values(
                id=code.id,
                member_id=code.member_id,
                code_hash=code.code_hash,
                created_at=code.created_at,
                expires_at=code.expires_at,
            )
        )

    async def consume(self, code_hash: str, now: datetime) -> UUID | None:
        # One statement: of two concurrent exchanges only one matches the row.
        result = await self._session.execute(
            update(login_codes)
            .where(
                login_codes.c.code_hash == code_hash,
                login_codes.c.consumed_at.is_(None),
                login_codes.c.cancelled_at.is_(None),
                login_codes.c.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(login_codes.c.member_id)
        )
        member_id: UUID | None = result.scalar_one_or_none()
        return member_id


class SqlSessions:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, session: AuthSession) -> None:
        await self._session.execute(
            auth_sessions.insert().values(
                id=session.id,
                member_id=session.member_id,
                created_at=session.created_at,
                expires_at=session.expires_at,
            )
        )

    async def get(self, session_id: UUID) -> AuthSession | None:
        row = (
            await self._session.execute(
                select(auth_sessions).where(auth_sessions.c.id == session_id)
            )
        ).one_or_none()
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
        await self._session.execute(
            update(auth_sessions)
            .where(
                auth_sessions.c.id == session_id,
                auth_sessions.c.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )

    async def revoke_all(self, member_id: UUID, now: datetime) -> None:
        await self._session.execute(
            update(auth_sessions)
            .where(
                auth_sessions.c.member_id == member_id,
                auth_sessions.c.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )


class SqlUnitOfWork(SqlAlchemyUnitOfWork):
    members: MemberRepository
    login_codes: LoginCodeRepository
    sessions: SessionRepository
    outbox: OutboxRepository

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        event_handlers: Sequence[EventHandler] = (),
    ) -> None:
        super().__init__(
            session_factory,
            stage_outbox=stage_identity_event,
            event_handlers=event_handlers,
        )

    def _attach(self, session: AsyncSession) -> None:
        self.members = SqlMembers(session, self.track)
        self.login_codes = SqlLoginCodes(session)
        self.sessions = SqlSessions(session)
        self.outbox = SqlOutbox(session)


class SqlRateLimiter:
    """Fixed-window counters in PostgreSQL; they survive restarts and rollbacks."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], clock: Callable[[], datetime]
    ) -> None:
        self._sessions = sessions
        self._clock = clock

    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        seconds = int(self._clock().timestamp())
        window = datetime.fromtimestamp(seconds - seconds % window_seconds, UTC)
        async with self._sessions() as session:
            count = await session.scalar(
                insert(rate_limits)
                .values(key=key, window_start=window, count=1)
                .on_conflict_do_update(
                    index_elements=[rate_limits.c.key, rate_limits.c.window_start],
                    set_={"count": rate_limits.c.count + 1},
                )
                .returning(rate_limits.c.count)
            )
            await session.execute(
                delete(rate_limits).where(
                    rate_limits.c.key == key, rate_limits.c.window_start < window
                )
            )
            await session.commit()
        return bool(count is not None and count <= limit)


def _member(row: Row[Any]) -> Member:
    return Member.reconstitute(
        MemberId(row.id),
        telegram_id=row.telegram_id,
        role=Role(row.role),
        status=MemberStatus(row.status),
        created_at=row.created_at,
        display_name=row.display_name,
        access_version=row.access_version,
        status_changed_at=row.status_changed_at,
        decided_by=row.decided_by,
    )
