"""The routing-key filter of a relay: each relay claims only the rows it can deliver."""

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import Select
from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import BinaryExpression, BindParameter, ColumnClause

from kernel_platform.outbox import OutboxColumns, SqlOutboxTx

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class OutboxModel(OutboxColumns, Base):
    __tablename__ = "outbox"


class RecordingSession:
    def __init__(self) -> None:
        self.statements: list[Select[tuple[OutboxModel]]] = []

    async def scalars(self, statement: Select[tuple[OutboxModel]]) -> list[object]:
        self.statements.append(statement)
        return []


async def claim(routing_keys: tuple[str, ...] | None) -> Select[tuple[OutboxModel]]:
    session = RecordingSession()
    tx = SqlOutboxTx(cast(AsyncSession, session), OutboxModel, routing_keys)

    await tx.claim_due(NOW, 20)

    (statement,) = session.statements
    return statement


def routing_conditions(statement: Select[tuple[OutboxModel]]) -> list[tuple[str, str]]:
    """(operator, value) of every comparison of `routing_key` in the WHERE clause."""
    return [
        (str(clause.operator.__name__), str(clause.right.value))
        for clause in visitors.iterate(statement.whereclause)
        if isinstance(clause, BinaryExpression)
        and isinstance(clause.left, ColumnClause)
        and clause.left.name == "routing_key"
        and isinstance(clause.right, BindParameter)
    ]


async def test_without_a_filter_every_due_row_is_claimed() -> None:
    assert routing_conditions(await claim(None)) == []


async def test_an_exact_key_claims_only_that_key() -> None:
    conditions = routing_conditions(await claim(("task.create",)))

    assert conditions == [("eq", "task.create")]


async def test_a_wildcard_claims_every_key_under_the_prefix() -> None:
    conditions = routing_conditions(await claim(("voice.*",)))

    assert conditions == [("startswith_op", "voice.")]


async def test_several_patterns_are_alternatives() -> None:
    conditions = routing_conditions(await claim(("task.create", "voice.*")))

    assert conditions == [("eq", "task.create"), ("startswith_op", "voice.")]


async def test_the_filter_keeps_the_due_unpublished_and_locking_rules() -> None:
    statement = await claim(("voice.*",))

    filtered = {
        column.name
        for column in visitors.iterate(statement.whereclause)
        if isinstance(column, ColumnClause)
    }
    assert {"published_at", "next_attempt_at", "routing_key"} <= filtered
    compiled = statement.compile(dialect=PGDialect())  # type: ignore[no-untyped-call]
    assert str(compiled).rstrip().endswith("FOR UPDATE SKIP LOCKED")
