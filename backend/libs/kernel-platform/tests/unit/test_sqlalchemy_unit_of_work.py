from dataclasses import dataclass
from typing import Any

import pytest
from kernel_domain.domain_event import DomainEvent
from kernel_domain.entity import Entity
from sqlalchemy.ext.asyncio import AsyncSession

from kernel_platform.sqlalchemy_unit_of_work import SqlAlchemyUnitOfWork


@dataclass(frozen=True, kw_only=True)
class Happened(DomainEvent):
    event_type: str = "happened"


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def commit(self) -> None:
        self.calls.append("commit")

    async def rollback(self) -> None:
        self.calls.append("rollback")

    async def close(self) -> None:
        self.calls.append("close")


class Unit(SqlAlchemyUnitOfWork):
    def __init__(self, session: FakeSession, **kwargs: Any) -> None:
        super().__init__(lambda: session, **kwargs)  # type: ignore[arg-type]


def aggregate() -> Entity[int]:
    entity = Entity(1)
    entity.add_domain_event(Happened())
    return entity


async def test_leaving_without_a_commit_rolls_back_and_closes() -> None:
    session = FakeSession()

    async with Unit(session):
        pass

    assert session.calls == ["rollback", "close"]


async def test_events_are_staged_before_the_commit_and_handled_after_it() -> None:
    session = FakeSession()
    order: list[str] = []

    async def stage(_: AsyncSession, event: DomainEvent) -> None:
        order.append(f"stage after {session.calls}")

    async def handle(event: DomainEvent) -> None:
        order.append(f"handle after {session.calls}")

    async with Unit(session, stage_outbox=stage, event_handlers=[handle]) as uow:
        entity = aggregate()
        uow.track(entity)
        uow.track(entity)  # tracked once
        await uow.commit()

    assert order == ["stage after []", "handle after ['commit']"]
    assert session.calls == ["commit", "close"]


async def test_a_failing_handler_does_not_undo_the_commit() -> None:
    session = FakeSession()

    async def broken(event: DomainEvent) -> None:
        raise OSError("redis down")

    async with Unit(session, event_handlers=[broken]) as uow:
        uow.track(aggregate())
        await uow.commit()

    assert session.calls == ["commit", "close"]


async def test_a_failing_stage_keeps_the_transaction_uncommitted() -> None:
    session = FakeSession()

    async def stage(_: AsyncSession, event: DomainEvent) -> None:
        raise ValueError("cannot stage")

    with pytest.raises(ValueError):
        async with Unit(session, stage_outbox=stage) as uow:
            uow.track(aggregate())
            await uow.commit()

    assert session.calls == ["rollback", "close"]


async def test_the_session_is_unavailable_outside_the_block() -> None:
    with pytest.raises(RuntimeError):
        await Unit(FakeSession()).commit()
