"""Identity persistence adapter, used within the caller's session."""

from collections.abc import Callable

from kernel_domain.entity import Entity
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from domain.entities.member import Member
from domain.value_objects.member_id import MemberId
from domain.value_objects.member_status import MemberStatus
from domain.value_objects.role import Role
from infrastructure.db.models import (
    LoginCodeModel,
    MemberModel,
)

# Serializes access decisions independently of the migration lock.
_DECISION_LOCK_KEY = 7_204_122


class SqlMembers:
    def __init__(
        self, session: AsyncSession, track: Callable[[Entity[MemberId]], None]
    ) -> None:
        self._session = session
        self._track = track

    async def add_if_absent(self, member: Member) -> bool:
        result = await self._session.execute(
            insert(MemberModel)
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
            .on_conflict_do_nothing(index_elements=[MemberModel.telegram_id])
            .returning(MemberModel.id)
        )
        return result.scalar_one_or_none() is not None

    async def get(self, member_id: MemberId, *, lock: bool = False) -> Member | None:
        query = select(MemberModel).where(MemberModel.id == member_id.value)
        if lock:
            query = query.with_for_update()
        return self._loaded((await self._session.scalars(query)).one_or_none())

    async def get_by_telegram_id(
        self, telegram_id: int, *, lock: bool = False
    ) -> Member | None:
        query = select(MemberModel).where(MemberModel.telegram_id == telegram_id)
        if lock:
            query = query.with_for_update()
        return self._loaded((await self._session.scalars(query)).one_or_none())

    async def get_by_login_code(
        self, code_hash: str, *, lock: bool = False
    ) -> Member | None:
        query = (
            select(MemberModel)
            .join(LoginCodeModel, LoginCodeModel.member_id == MemberModel.id)
            .where(LoginCodeModel.code_hash == code_hash)
        )
        if lock:
            query = query.with_for_update(of=MemberModel)
        return self._loaded((await self._session.scalars(query)).one_or_none())

    async def lock_decisions(self) -> None:
        await self._session.execute(
            select(func.pg_advisory_xact_lock(_DECISION_LOCK_KEY))
        )

    async def count_approved_admins(self) -> int:
        count = await self._session.scalar(
            select(func.count())
            .select_from(MemberModel)
            .where(
                MemberModel.role == Role.ADMIN.value,
                MemberModel.status == MemberStatus.APPROVED.value,
            )
        )
        return int(count or 0)

    async def list_by_status(
        self, statuses: tuple[MemberStatus, ...], *, limit: int, offset: int
    ) -> list[Member]:
        rows = await self._session.scalars(
            select(MemberModel)
            .where(MemberModel.status.in_([status.value for status in statuses]))
            .order_by(MemberModel.created_at, MemberModel.id)
            .limit(limit)
            .offset(offset)
        )
        return [_member(row) for row in rows]

    async def update_access(self, member: Member, *, loaded_version: int) -> bool:
        result = await self._session.execute(
            update(MemberModel)
            .where(
                MemberModel.id == member.id.value,
                MemberModel.access_version == loaded_version,
            )
            .values(
                status=member.status.value,
                display_name=member.display_name,
                access_version=member.access_version,
                status_changed_at=member.status_changed_at,
                decided_by=member.decided_by,
            )
            .returning(MemberModel.id)
        )
        if result.scalar_one_or_none() is None:
            return False
        self._track(member)  # its events are staged by the commit
        return True

    def _loaded(self, row: MemberModel | None) -> Member | None:
        return _member(row) if row else None


def _member(row: MemberModel) -> Member:
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
