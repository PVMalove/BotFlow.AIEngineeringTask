# ruff: noqa: E501
"""Unit of Work: транзакционная граница вокруг command handler'ов (ADR 0006).

Живёт рядом с outbox/drain.py — инфраструктурная забота про сессию/
транзакцию, не про домен (тот же прецедент, что ADR 0006 уже применило к
repository-портам). `UnitOfWork` не знает ни об одном конкретном
репозитории; каждый сервис расширяет его собственным Protocol с
атрибутами-репозиториями этого сервиса."""

from types import TracebackType
from typing import Protocol, Self

from sqlalchemy.ext.asyncio import AsyncSession


class UnitOfWork(Protocol):
    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class SqlAlchemyUnitOfWork:
    """Общая бухгалтерия, переиспользуемая per-service реализациями:
    переиспользует уже существующую request-scoped `AsyncSession` (не
    создаёт свою через `session_factory()`), никогда её не закрывает —
    жизненный цикл сессии остаётся за существующим teardown (`get_db_session`)
    — и откатывает транзакцию по умолчанию, если `commit()` не был вызван
    явно ни через штатный возврат, ни через исключение."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._committed = False

    async def __aenter__(self) -> Self:
        self._committed = False
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        if not self._committed:
            await self._session.rollback()

    async def commit(self) -> None:
        await self._session.commit()
        self._committed = True

    async def rollback(self) -> None:
        await self._session.rollback()
        self._committed = False
