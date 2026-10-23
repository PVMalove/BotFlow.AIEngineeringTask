"""The routing-key filter of a relay: each relay claims only the rows it can deliver."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.dialects import postgresql

from kernel_platform.outbox import SqlOutboxTx, outbox_table

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
TABLE = outbox_table(MetaData())


class Session:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> list[Any]:
        self.statements.append(statement)
        return []


async def claimed_sql(routing_keys: tuple[str, ...] | None) -> str:
    session = Session()
    tx = SqlOutboxTx(session, TABLE, routing_keys)  # type: ignore[arg-type]

    await tx.claim_due(NOW, 20)

    (statement,) = session.statements
    compiled = statement.compile(
        dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
        compile_kwargs={"literal_binds": True},
    )
    return " ".join(str(compiled).split())


async def test_without_a_filter_every_due_row_is_claimed() -> None:
    sql = await claimed_sql(None)

    assert "routing_key" not in sql.split("WHERE")[1]


async def test_an_exact_key_claims_only_that_key() -> None:
    sql = await claimed_sql(("task.create",))

    assert "outbox.routing_key = 'task.create'" in sql


async def test_a_wildcard_claims_every_key_under_the_prefix() -> None:
    sql = await claimed_sql(("voice.*",))

    assert "outbox.routing_key LIKE 'voice.' ||" in sql
    assert "outbox.routing_key = " not in sql


async def test_several_patterns_are_alternatives() -> None:
    sql = await claimed_sql(("task.create", "voice.*"))

    assert "outbox.routing_key = 'task.create' OR" in sql
    assert "outbox.routing_key LIKE 'voice.' ||" in sql


async def test_the_filter_keeps_the_due_unpublished_and_locking_rules() -> None:
    sql = await claimed_sql(("voice.*",))

    assert "published_at IS NULL" in sql
    assert "next_attempt_at <=" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
