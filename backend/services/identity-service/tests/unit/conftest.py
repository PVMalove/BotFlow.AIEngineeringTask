"""In-memory ports with transaction semantics: nothing is visible before commit."""

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self
from uuid import UUID

from domain.entities.auth import Member

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@dataclass
class Store:
    members: dict[UUID, Member] = field(default_factory=dict)


class FakeMembers:
    def __init__(self, data: Store) -> None:
        self._data = data

    async def add_if_absent(self, member: Member) -> bool:
        if any(
            existing.telegram_id == member.telegram_id
            for existing in self._data.members.values()
        ):
            return False
        self._data.members[member.id] = member
        return True

    async def get_by_telegram_id(
        self, telegram_id: int, *, lock: bool = False
    ) -> Member | None:
        return next(
            (m for m in self._data.members.values() if m.telegram_id == telegram_id),
            None,
        )


class FakeUnitOfWork:
    def __init__(self, store: Store) -> None:
        self._store = store
        self._working = Store()
        self.members = FakeMembers(self._working)

    async def __aenter__(self) -> Self:
        self._working.members = deepcopy(self._store.members)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        self._store.members = deepcopy(self._working.members)


class Clock:
    def __init__(self) -> None:
        self.current = NOW

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **delta: float) -> None:
        self.current += timedelta(**delta)
