"""ORM mappings stay in infrastructure; repositories work in the unit's session."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from sqlalchemy import Uuid, inspect
from sqlalchemy.ext.asyncio import AsyncSession

from domain.entities.task import Task
from domain.value_objects.task_id import TaskId
from infrastructure.db.models import Base
from infrastructure.db.unit_of_work import SqlTasksUnitOfWork


def test_models_declare_the_service_tables_with_uuid_keys() -> None:
    assert set(Base.metadata.tables) == {"tasks", "inbox_messages", "outbox"}
    assert isinstance(Base.metadata.tables["tasks"].c.id.type, Uuid)
    assert isinstance(Base.metadata.tables["outbox"].c.id.type, Uuid)


def test_domain_entities_are_not_mapped() -> None:
    assert inspect(Task, raiseerr=False) is None


async def test_uow_repositories_work_in_the_units_session() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = None
    session.execute.return_value = Mock(scalar_one_or_none=Mock(return_value=None))
    uow = SqlTasksUnitOfWork(Mock(return_value=session))
    async with uow:
        assert await uow.tasks.get(TaskId(uuid4())) is None
        assert await uow.inbox.claim("message") is False

    session.get.assert_awaited_once()
    session.execute.assert_awaited_once()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.close.assert_awaited_once()
