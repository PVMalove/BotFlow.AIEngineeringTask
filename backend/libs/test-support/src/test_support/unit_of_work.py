"""Общий `FakeUnitOfWork` test-double (ADR 0006) — async context manager без
реальной БД, с флагами `committed`/`rolled_back` вместо проверки вызовов на
моке. Каждый сервис расширяет его своими fake-репозиториями, зеркаля
generic/per-service разделение реального `kernel_platform.unit_of_work`."""

from types import TracebackType
from typing import Self


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        if not self.committed:
            self.rolled_back = True

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True
        self.committed = False
