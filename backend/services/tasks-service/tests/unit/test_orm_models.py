"""Infrastructure mappings preserve table identity and keep domain entities unmapped."""

from sqlalchemy import inspect

from infrastructure.db.models import Base, InboxMessageModel, TaskModel
from infrastructure.db.tables import inbox_messages, metadata, tasks


def test_models_share_the_migration_metadata() -> None:
    assert Base.metadata is metadata
    assert inspect(TaskModel).local_table is tasks
    assert inspect(InboxMessageModel).local_table is inbox_messages
    assert inspect(TaskModel).primary_key[0].type.python_type.__name__ == "UUID"


async def test_uow_attaches_every_repository_to_one_session() -> None:
    from unittest.mock import AsyncMock, Mock

    from sqlalchemy.ext.asyncio import AsyncSession

    from infrastructure.db.unit_of_work import SqlTasksUnitOfWork

    session = AsyncMock(spec=AsyncSession)
    uow = SqlTasksUnitOfWork(Mock(return_value=session))
    async with uow:
        repositories = [uow.tasks, uow.inbox]
        assert all(
            vars(repository)["_session"] is session for repository in repositories
        )
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.close.assert_awaited_once()
