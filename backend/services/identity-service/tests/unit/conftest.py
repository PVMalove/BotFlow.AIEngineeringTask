"""In-memory ports with transaction semantics: nothing is visible before commit."""

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self
from uuid import UUID

from application.ports import TokenClaims
from domain.entities.auth import AuthSession, InvalidSession, LoginCode, Member

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@dataclass
class CodeRow:
    code: LoginCode
    consumed_at: datetime | None = None
    cancelled_at: datetime | None = None


@dataclass
class Store:
    members: dict[UUID, Member] = field(default_factory=dict)
    codes: dict[UUID, CodeRow] = field(default_factory=dict)
    sessions: dict[UUID, AuthSession] = field(default_factory=dict)


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

    async def get(self, member_id: UUID) -> Member | None:
        return self._data.members.get(member_id)

    async def get_by_telegram_id(
        self, telegram_id: int, *, lock: bool = False
    ) -> Member | None:
        return next(
            (m for m in self._data.members.values() if m.telegram_id == telegram_id),
            None,
        )


class FakeLoginCodes:
    def __init__(self, data: Store) -> None:
        self._data = data

    async def cancel_active(self, member_id: UUID, now: datetime) -> None:
        for row in self._data.codes.values():
            if (
                row.code.member_id == member_id
                and row.consumed_at is None
                and row.cancelled_at is None
            ):
                row.cancelled_at = now

    async def add(self, code: LoginCode) -> None:
        self._data.codes[code.id] = CodeRow(code)

    async def consume(self, code_hash: str, now: datetime) -> UUID | None:
        for row in self._data.codes.values():
            if (
                row.code.code_hash == code_hash
                and row.consumed_at is None
                and row.cancelled_at is None
                and row.code.expires_at > now
            ):
                row.consumed_at = now
                return row.code.member_id
        return None


class FakeSessions:
    def __init__(self, data: Store) -> None:
        self._data = data

    async def add(self, session: AuthSession) -> None:
        self._data.sessions[session.id] = session

    async def get(self, session_id: UUID) -> AuthSession | None:
        return self._data.sessions.get(session_id)

    async def revoke(self, session_id: UUID, now: datetime) -> None:
        session = self._data.sessions.get(session_id)
        if session is not None and session.revoked_at is None:
            self._data.sessions[session_id] = replace(session, revoked_at=now)


class FakeUnitOfWork:
    def __init__(self, store: Store) -> None:
        self._store = store
        self._working = Store()
        self.members = FakeMembers(self._working)
        self.login_codes = FakeLoginCodes(self._working)
        self.sessions = FakeSessions(self._working)

    async def __aenter__(self) -> Self:
        self._working.members = deepcopy(self._store.members)
        self._working.codes = deepcopy(self._store.codes)
        self._working.sessions = deepcopy(self._store.sessions)
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
        self._store.codes = deepcopy(self._working.codes)
        self._store.sessions = deepcopy(self._working.sessions)


class FakeRateLimiter:
    """Fixed count per key; the real adapter owns window arithmetic."""

    def __init__(self) -> None:
        self.hits: Counter[str] = Counter()

    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        self.hits[key] += 1
        return self.hits[key] <= limit


class FakeTokens:
    """Opaque tokens; the real RS256 adapter has its own tests."""

    def __init__(self) -> None:
        self.issued: dict[str, TokenClaims] = {}

    def issue(self, session: AuthSession) -> str:
        token = f"token-{session.id}"
        self.issued[token] = TokenClaims(session.id, session.member_id)
        return token

    def verify(self, token: str) -> TokenClaims:
        try:
            return self.issued[token]
        except KeyError:
            raise InvalidSession from None


class Clock:
    def __init__(self) -> None:
        self.current = NOW

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **delta: float) -> None:
        self.current += timedelta(**delta)
