"""ORM mappings stay in infrastructure; repositories work in the unit's session."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from sqlalchemy import Uuid, inspect
from sqlalchemy.ext.asyncio import AsyncSession

from domain.entities.job import Job
from domain.value_objects.intent_id import IntentId
from infrastructure.db.models import Base, JobModel, VoiceJobModel
from infrastructure.db.unit_of_work import SqlAutomationUnitOfWork


def test_models_declare_the_service_tables_with_uuid_keys() -> None:
    assert set(Base.metadata.tables) == {
        "telegram_updates",
        "jobs",
        "voice_jobs",
        "outbox",
        "inbox_messages",
        "notification_intents",
    }
    assert isinstance(Base.metadata.tables["jobs"].c.id.type, Uuid)
    assert isinstance(Base.metadata.tables["voice_jobs"].c.job_id.type, Uuid)


def test_domain_entities_are_not_mapped() -> None:
    assert inspect(Job, raiseerr=False) is None


def test_voice_is_an_explicit_one_to_one_relationship() -> None:
    relation = inspect(JobModel).relationships["voice"]
    assert relation.uselist is False
    assert relation.mapper.class_ is VoiceJobModel
    assert relation.back_populates == "job"


async def test_uow_repositories_work_in_the_units_session() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = Mock(scalar_one_or_none=Mock(return_value=None))
    session.scalars.return_value = Mock(one_or_none=Mock(return_value=None))
    uow = SqlAutomationUnitOfWork(Mock(return_value=session))
    async with uow:
        await uow.jobs.lock_acceptance()
        assert await uow.inbox.claim("message") is False
        assert await uow.intents.get(IntentId(uuid4())) is None

    assert session.execute.await_count == 2
    session.scalars.assert_awaited_once()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.close.assert_awaited_once()
