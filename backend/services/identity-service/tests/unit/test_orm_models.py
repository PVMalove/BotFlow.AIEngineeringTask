"""Infrastructure mappings preserve table identity and keep domain entities unmapped."""

from sqlalchemy import inspect

from infrastructure.db.models import Base, LoginCodeModel, MemberModel
from infrastructure.db.tables import login_codes, members, metadata


def test_models_share_the_migration_metadata() -> None:
    assert Base.metadata is metadata
    assert inspect(MemberModel).local_table is members
    assert inspect(LoginCodeModel).local_table is login_codes
    assert inspect(MemberModel).primary_key[0].type.python_type.__name__ == "UUID"


async def test_uow_attaches_every_repository_to_one_session() -> None:
    from unittest.mock import AsyncMock, Mock

    from sqlalchemy.ext.asyncio import AsyncSession

    from infrastructure.db.unit_of_work import SqlUnitOfWork

    session = AsyncMock(spec=AsyncSession)
    uow = SqlUnitOfWork(Mock(return_value=session))
    async with uow:
        repositories = [uow.members, uow.login_codes, uow.sessions, uow.outbox]
        assert all(
            vars(repository)["_session"] is session for repository in repositories
        )
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.close.assert_awaited_once()
