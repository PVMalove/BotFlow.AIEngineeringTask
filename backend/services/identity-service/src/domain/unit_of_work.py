"""Domain unit-of-work port: commit is explicit, rollback is the default."""

from types import TracebackType
from typing import Protocol, Self

from domain.repositories import MemberRepository


class UnitOfWork(Protocol):
    @property
    def members(self) -> MemberRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...
