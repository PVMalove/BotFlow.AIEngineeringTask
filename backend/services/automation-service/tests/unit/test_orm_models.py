"""Infrastructure mappings preserve table identity and keep domain entities unmapped."""

from sqlalchemy import inspect

from infrastructure.db.models import Base, JobModel, VoiceJobModel
from infrastructure.db.tables import jobs, metadata, voice_jobs


def test_models_share_the_migration_metadata() -> None:
    assert Base.metadata is metadata
    assert inspect(JobModel).local_table is jobs
    assert inspect(VoiceJobModel).local_table is voice_jobs
    assert inspect(JobModel).primary_key[0].type.python_type.__name__ == "UUID"


def test_voice_is_an_explicit_one_to_one_relationship() -> None:
    relation = inspect(JobModel).relationships["voice"]
    assert relation.uselist is False
    assert relation.mapper.class_ is VoiceJobModel
    assert relation.back_populates == "job"


async def test_uow_attaches_every_repository_to_one_session() -> None:
    from unittest.mock import AsyncMock, Mock

    from sqlalchemy.ext.asyncio import AsyncSession

    from infrastructure.db.unit_of_work import SqlAutomationUnitOfWork

    session = AsyncMock(spec=AsyncSession)
    uow = SqlAutomationUnitOfWork(Mock(return_value=session))
    async with uow:
        repositories = [uow.jobs, uow.intents, uow.inbox]
        assert all(
            vars(repository)["_session"] is session for repository in repositories
        )
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.close.assert_awaited_once()
