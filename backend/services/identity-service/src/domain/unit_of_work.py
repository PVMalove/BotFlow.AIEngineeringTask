"""Transaction contract of the identity service; rollback is the default."""

from typing import Protocol

from kernel_platform.unit_of_work import UnitOfWork

from domain.repositories import (
    LoginCodeRepository,
    MemberRepository,
    OutboxRepository,
    SessionRepository,
)


class IdentityUnitOfWork(UnitOfWork, Protocol):
    members: MemberRepository
    login_codes: LoginCodeRepository
    sessions: SessionRepository
    outbox: OutboxRepository
