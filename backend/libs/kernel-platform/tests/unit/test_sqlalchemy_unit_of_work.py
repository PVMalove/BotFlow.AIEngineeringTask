"""Atomic storage and event delivery through the public Unit of Work contract."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from kernel_domain.domain_event import DomainEvent
from kernel_domain.entity import Entity
from sqlalchemy.ext.asyncio import AsyncSession

from kernel_platform.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork


@pytest.mark.parametrize("fail", [False, True])
async def test_exit_without_commit_rolls_back_and_closes(fail: bool) -> None:
    session = AsyncMock(spec=AsyncSession)
    uow = SqlAlchemyUnitOfWork(Mock(return_value=session))
    try:
        async with uow:
            if fail:
                raise ValueError("operation failed")
    except ValueError:
        pass
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()
    session.close.assert_awaited_once()


@pytest.mark.parametrize("failure", [None, "stage", "commit"])
async def test_outbox_is_staged_before_commit_and_handlers_run_only_after_it(
    failure: str | None,
) -> None:
    operations: list[str] = []
    event = DomainEvent()
    entity = Entity(uuid4())
    entity.add_domain_event(event)
    session = AsyncMock(spec=AsyncSession)

    async def stage(actual_session: AsyncSession, actual_event: DomainEvent) -> None:
        assert actual_session is session
        assert actual_event is event
        operations.append("stage")
        if failure == "stage":
            raise ValueError("staging failed")

    async def commit() -> None:
        operations.append("commit")
        if failure == "commit":
            raise ValueError("commit failed")

    async def deliver(actual_event: DomainEvent) -> None:
        assert actual_event is event
        operations.append("deliver")

    session.commit.side_effect = commit
    uow = SqlAlchemyUnitOfWork(
        Mock(return_value=session), stage_outbox=stage, event_handlers=[deliver]
    )
    if failure:
        with pytest.raises(ValueError):
            async with uow:
                uow.track(entity)
                await uow.commit()
        session.rollback.assert_awaited_once()
        assert operations == (["stage"] if failure == "stage" else ["stage", "commit"])
    else:
        async with uow:
            uow.track(entity)
            uow.track(entity)  # one event despite loading the same aggregate twice
            await uow.commit()
        session.rollback.assert_not_awaited()
        assert operations == ["stage", "commit", "deliver"]
    session.close.assert_awaited_once()


async def test_a_failing_handler_does_not_undo_the_commit() -> None:
    session = AsyncMock(spec=AsyncSession)
    handler = AsyncMock(side_effect=OSError("redis down"))
    uow = SqlAlchemyUnitOfWork(Mock(return_value=session), event_handlers=[handler])
    entity = Entity(uuid4())
    entity.add_domain_event(DomainEvent())
    async with uow:
        uow.track(entity)
        await uow.commit()
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    session.close.assert_awaited_once()


async def test_commit_is_unavailable_outside_the_block() -> None:
    uow = SqlAlchemyUnitOfWork(Mock())
    with pytest.raises(RuntimeError):
        await uow.commit()
