"""Domain unit-of-work port: commit is explicit, rollback is the default."""

from types import TracebackType
from typing import Protocol, Self

from domain.repositories import (
    LoginCodeRepository,
    MemberRepository,
    OutboxRepository,
    SessionRepository,
)


class UnitOfWork(Protocol):
    @property
    def members(self) -> MemberRepository: ...

    @property
    def login_codes(self) -> LoginCodeRepository: ...

    @property
    def sessions(self) -> SessionRepository: ...

    @property
    def outbox(self) -> OutboxRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...
