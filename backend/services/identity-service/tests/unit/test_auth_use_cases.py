"""Identity authentication use cases against in-memory ports."""

import pytest
from conftest import Clock, FakeUnitOfWork, Store

from application.commands.auth import EnsureInitialAdmin
from domain.entities.auth import MemberStatus, Role

ADMIN_TELEGRAM_ID = 4_200_000_001


@pytest.fixture
def store() -> Store:
    return Store()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def ensure_admin(store: Store, clock: Clock) -> EnsureInitialAdmin:
    return EnsureInitialAdmin(lambda: FakeUnitOfWork(store), clock)


async def test_initial_admin_is_created_approved(
    ensure_admin: EnsureInitialAdmin, store: Store
) -> None:
    created = await ensure_admin(ADMIN_TELEGRAM_ID)

    (member,) = store.members.values()
    assert created is True
    assert member.telegram_id == ADMIN_TELEGRAM_ID
    assert (member.role, member.status) == (Role.ADMIN, MemberStatus.APPROVED)


async def test_repeat_keeps_existing_access_decision(
    ensure_admin: EnsureInitialAdmin, store: Store
) -> None:
    await ensure_admin(ADMIN_TELEGRAM_ID)
    (member,) = store.members.values()
    store.members[member.id] = member.with_status(MemberStatus.BLOCKED)

    created = await ensure_admin(ADMIN_TELEGRAM_ID)

    (after,) = store.members.values()
    assert created is False
    assert after.status is MemberStatus.BLOCKED
    assert after.id == member.id
