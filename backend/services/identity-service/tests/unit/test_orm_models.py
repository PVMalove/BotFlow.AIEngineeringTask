"""ORM mappings stay in infrastructure; repositories work in the unit's session."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from sqlalchemy import Uuid, inspect
from sqlalchemy.ext.asyncio import AsyncSession

from domain.entities.member import Member
from domain.value_objects.member_id import MemberId
from infrastructure.db.models import Base
from infrastructure.db.unit_of_work import SqlUnitOfWork

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_models_declare_the_service_tables_with_uuid_keys() -> None:
    assert set(Base.metadata.tables) == {
        "members",
        "login_codes",
        "auth_sessions",
        "rate_limits",
        "outbox",
    }
    assert isinstance(Base.metadata.tables["members"].c.id.type, Uuid)
    assert isinstance(Base.metadata.tables["login_codes"].c.member_id.type, Uuid)


def test_domain_entities_are_not_mapped() -> None:
    assert inspect(Member, raiseerr=False) is None


async def test_uow_repositories_work_in_the_units_session() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalars.return_value = Mock(one_or_none=Mock(return_value=None))
    uow = SqlUnitOfWork(Mock(return_value=session))
    async with uow:
        await uow.members.lock_decisions()
        assert await uow.members.get(MemberId(uuid4())) is None
        await uow.login_codes.cancel_active(uuid4(), NOW)
        await uow.sessions.revoke(uuid4(), NOW)

    assert session.execute.await_count == 3
    session.scalars.assert_awaited_once()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.close.assert_awaited_once()
