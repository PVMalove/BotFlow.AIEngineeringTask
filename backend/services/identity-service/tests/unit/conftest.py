"""In-memory ports with transaction semantics: nothing is visible before commit."""

from collections import Counter
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self
from uuid import UUID

from kernel_domain.result import Result

from application.ports import TokenClaims
from domain.entities.auth_session import AuthSession
from domain.entities.login_code import LoginCode
from domain.entities.member import Member
from domain.errors import IdentityErrors
from domain.outbox import OutboxEntry
from domain.repositories import (
    LoginCodeRepository,
    MemberRepository,
    OutboxRepository,
    SessionRepository,
)
from domain.value_objects.member_id import MemberId
from domain.value_objects.member_status import MemberStatus
from domain.value_objects.role import Role
from infrastructure.db.outbox_events import outbox_entry_for

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@dataclass
class CodeRow:
    code: LoginCode
    consumed_at: datetime | None = None
    cancelled_at: datetime | None = None


@dataclass
class Store:
    members: dict[MemberId, Member] = field(default_factory=dict)
    codes: dict[UUID, CodeRow] = field(default_factory=dict)
    sessions: dict[UUID, AuthSession] = field(default_factory=dict)
    outbox: dict[UUID, OutboxEntry] = field(default_factory=dict)


def with_status(member: Member, status: MemberStatus) -> Member:
    """The same member in another status (a test double of an admin's decision)."""
    return Member.reconstitute(
        member.id,
        telegram_id=member.telegram_id,
        role=member.role,
        status=status,
        created_at=member.created_at,
        display_name=member.display_name,
        access_version=member.access_version,
        status_changed_at=member.status_changed_at,
        decided_by=member.decided_by,
    )


class FakeMembers:
    def __init__(self, data: Store, track: Callable[[Member], None]) -> None:
        self._data = data
        self._track = track

    async def add_if_absent(self, member: Member) -> bool:
        if any(
            existing.telegram_id == member.telegram_id
            for existing in self._data.members.values()
        ):
            return False
        self._data.members[member.id] = member
        return True

    async def get(self, member_id: MemberId, *, lock: bool = False) -> Member | None:
        return self._loaded(self._data.members.get(member_id))

    async def get_by_telegram_id(
        self, telegram_id: int, *, lock: bool = False
    ) -> Member | None:
        return self._loaded(
            next(
                (
                    m
                    for m in self._data.members.values()
                    if m.telegram_id == telegram_id
                ),
                None,
            )
        )

    async def get_by_login_code(
        self, code_hash: str, *, lock: bool = False
    ) -> Member | None:
        row = next(
            (r for r in self._data.codes.values() if r.code.code_hash == code_hash),
            None,
        )
        return self._loaded(
            self._data.members.get(MemberId(row.code.member_id)) if row else None
        )

    async def lock_decisions(self) -> None:
        return None

    async def count_approved_admins(self) -> int:
        return sum(
            m.role is Role.ADMIN and m.status is MemberStatus.APPROVED
            for m in self._data.members.values()
        )

    async def list_by_status(
        self, statuses: tuple[MemberStatus, ...], *, limit: int, offset: int
    ) -> list[Member]:
        rows = sorted(
            (m for m in self._data.members.values() if m.status in statuses),
            key=lambda m: (m.created_at, m.id.value),
        )
        return rows[offset : offset + limit]

    async def update_access(self, member: Member, *, loaded_version: int) -> bool:
        stored = self._data.members.get(member.id)
        if stored is None or stored.access_version != loaded_version:
            return False
        self._data.members[member.id] = with_status(member, member.status)
        self._track(member)
        return True

    def _loaded(self, member: Member | None) -> Member | None:
        """A separate load, like a session of its own: the row keeps its version."""
        if member is None:
            return None
        loaded = with_status(member, member.status)
        self._track(loaded)
        return loaded


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
            session.revoked_at = now

    async def revoke_all(self, member_id: UUID, now: datetime) -> None:
        for session in self._data.sessions.values():
            if session.member_id == member_id and session.revoked_at is None:
                session.revoked_at = now


class FakeOutbox:
    def __init__(self, data: Store) -> None:
        self._data = data

    async def add(self, entry: OutboxEntry) -> None:
        self._data.outbox[entry.id] = entry

    async def claim_due(self, now: datetime, limit: int) -> list[OutboxEntry]:
        due = [
            e
            for e in self._data.outbox.values()
            if e.published_at is None
            and e.next_attempt_at is not None
            and e.next_attempt_at <= now
        ]
        return sorted(due, key=lambda e: (e.created_at, e.id))[:limit]

    async def mark_published(self, entry_id: UUID, now: datetime) -> None:
        entry = self._data.outbox[entry_id]
        self._data.outbox[entry_id] = OutboxEntry(
            entry.id,
            entry.type,
            entry.payload,
            entry.created_at,
            entry.attempts,
            entry.next_attempt_at,
            now,
        )

    async def reschedule(
        self, entry_id: UUID, attempts: int, next_attempt_at: datetime | None
    ) -> None:
        entry = self._data.outbox[entry_id]
        self._data.outbox[entry_id] = OutboxEntry(
            entry.id,
            entry.type,
            entry.payload,
            entry.created_at,
            attempts,
            next_attempt_at,
            entry.published_at,
        )


class FakeUnitOfWork:
    """Works on a copy of the store; a commit stages the events, then publishes it."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._working = Store()
        self._tracked: list[Member] = []
        self.members: MemberRepository = FakeMembers(
            self._working, self._tracked.append
        )
        self.login_codes: LoginCodeRepository = FakeLoginCodes(self._working)
        self.sessions: SessionRepository = FakeSessions(self._working)
        self.outbox: OutboxRepository = FakeOutbox(self._working)

    async def __aenter__(self) -> Self:
        self._working.members = deepcopy(self._store.members)
        self._working.codes = deepcopy(self._store.codes)
        self._working.sessions = deepcopy(self._store.sessions)
        self._working.outbox = deepcopy(self._store.outbox)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        for member in self._tracked:
            for event in member.pull_events():
                entry = outbox_entry_for(event)
                if entry is not None:
                    self._working.outbox[entry.id] = entry
        self._store.members = deepcopy(self._working.members)
        self._store.codes = deepcopy(self._working.codes)
        self._store.sessions = deepcopy(self._working.sessions)
        self._store.outbox = deepcopy(self._working.outbox)

    async def rollback(self) -> None:
        return None


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

    def verify(self, token: str) -> Result[TokenClaims]:
        claims = self.issued.get(token)
        if claims is None:
            return Result[TokenClaims].fail(IdentityErrors.invalid_session())
        return Result[TokenClaims].ok(claims)


class Clock:
    def __init__(self) -> None:
        self.current = NOW

    def now(self) -> datetime:
        return self.current

    def advance(self, **delta: float) -> None:
        self.current += timedelta(**delta)
