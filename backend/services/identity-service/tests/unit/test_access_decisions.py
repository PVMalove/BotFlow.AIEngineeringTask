"""The Member decision table and the DecideMember handler against in-memory ports."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from conftest import NOW, Clock, CodeRow, FakeUnitOfWork, Store
from kernel_domain.errors import ErrorType
from kernel_domain.result import Result

from application.commands.access import DecideMemberCommand, DecideMemberCommandHandler
from contracts.views import DecisionView
from domain.entities.auth_session import AuthSession
from domain.entities.login_code import LoginCode
from domain.entities.member import Member
from domain.events.member_domain_event import (
    MemberApproved,
    MemberBlocked,
    MemberEvent,
    MemberRejected,
    MemberUnblocked,
)
from domain.value_objects.access import Action
from domain.value_objects.member_id import MemberId
from domain.value_objects.member_status import MemberStatus
from domain.value_objects.role import Role

S = MemberStatus


def member(
    status: MemberStatus, role: Role = Role.USER, version: int = 1, telegram_id: int = 0
) -> Member:
    return Member.reconstitute(
        MemberId.new_id(),
        telegram_id=telegram_id or uuid4().int % 10**9,
        role=role,
        status=status,
        created_at=NOW,
        display_name=None,
        access_version=version,
        status_changed_at=None,
        decided_by=None,
    )


def apply(target: Member, action: Action, **overrides: object) -> Result[bool]:
    values = dict(
        actor_id=uuid4(),
        approved_admins=2,
        expected_version=target.access_version,
        now=NOW + timedelta(minutes=1),
    )
    values.update(overrides)
    return target.decide(action, **values)  # type: ignore[arg-type]


def error_code(result: Result[bool]) -> str:
    assert result.is_err
    return result.error.code


@pytest.mark.parametrize(
    ("action", "before", "after", "event"),
    [
        (Action.APPROVE, S.PENDING, S.APPROVED, MemberApproved),
        (Action.REJECT, S.PENDING, S.REJECTED, MemberRejected),
        (Action.BLOCK, S.PENDING, S.BLOCKED, MemberBlocked),
        (Action.BLOCK, S.REJECTED, S.BLOCKED, MemberBlocked),
        (Action.BLOCK, S.APPROVED, S.BLOCKED, MemberBlocked),
        (Action.UNBLOCK, S.BLOCKED, S.APPROVED, MemberUnblocked),
    ],
)
def test_allowed_transitions_bump_the_version_record_the_decision_and_raise_an_event(
    action: Action, before: MemberStatus, after: MemberStatus, event: type
) -> None:
    target = member(before, version=4)
    actor = uuid4()

    result = apply(target, action, actor_id=actor)

    assert result.is_ok and result.value is True
    assert target.status is after
    assert target.access_version == 5
    assert target.decided_by == actor
    assert target.status_changed_at == NOW + timedelta(minutes=1)
    (raised,) = target.pull_events()
    assert isinstance(raised, event) and isinstance(raised, MemberEvent)
    assert (raised.member_id, raised.access_version, raised.decided_by) == (
        target.id,
        5,
        actor,
    )


@pytest.mark.parametrize(
    ("action", "before"),
    [
        (Action.APPROVE, S.APPROVED),
        (Action.UNBLOCK, S.APPROVED),
        (Action.REJECT, S.REJECTED),
        (Action.BLOCK, S.BLOCKED),
    ],
)
def test_a_repeat_changes_nothing_even_with_a_stale_version(
    action: Action, before: MemberStatus
) -> None:
    target = member(before, version=4)

    result = apply(target, action, expected_version=1)

    assert result.is_ok and result.value is False
    assert (target.status, target.access_version) == (before, 4)
    assert target.pull_events() == []


@pytest.mark.parametrize(
    ("action", "before"),
    [
        (Action.APPROVE, S.REJECTED),
        (Action.APPROVE, S.BLOCKED),
        (Action.REJECT, S.APPROVED),
        (Action.REJECT, S.BLOCKED),
        (Action.UNBLOCK, S.PENDING),
        (Action.UNBLOCK, S.REJECTED),
    ],
)
def test_other_transitions_are_invalid(action: Action, before: MemberStatus) -> None:
    result = apply(member(before), action)

    assert error_code(result) == "invalid_transition"


def test_a_stale_version_is_a_conflict() -> None:
    result = apply(member(S.PENDING, version=2), Action.APPROVE, expected_version=1)

    assert result.is_err and result.error.type is ErrorType.CONFLICT
    assert error_code(result) == "version_conflict"


def test_an_admin_cannot_block_themselves() -> None:
    admin = member(S.APPROVED, Role.ADMIN)

    result = apply(admin, Action.BLOCK, actor_id=admin.id.value)

    assert error_code(result) == "cannot_block_self"


def test_the_last_approved_admin_cannot_be_blocked() -> None:
    result = apply(member(S.APPROVED, Role.ADMIN), Action.BLOCK, approved_admins=1)

    assert error_code(result) == "last_admin"


def test_a_non_admin_or_unapproved_admin_is_not_protected_by_the_guard() -> None:
    assert apply(member(S.APPROVED), Action.BLOCK, approved_admins=1).value is True
    result = apply(member(S.PENDING, Role.ADMIN), Action.BLOCK, approved_admins=1)
    assert result.value is True


def test_a_rejected_member_who_joins_again_is_reopened() -> None:
    rejected = member(S.REJECTED, version=3)
    rejected.decided_by = uuid4()

    outcome = rejected.join_again("New name", NOW)

    assert outcome.value == "submitted"
    assert (rejected.status, rejected.access_version) == (S.PENDING, 4)
    assert rejected.display_name == "New name"
    assert rejected.decided_by is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (S.PENDING, "already_pending"),
        (S.APPROVED, "already_member"),
        (S.BLOCKED, "blocked"),
    ],
)
def test_a_repeated_join_changes_nobody_else(
    status: MemberStatus, expected: str
) -> None:
    target = member(status, version=3)

    outcome = target.join_again("Other", NOW)

    assert outcome.value == expected
    assert (target.status, target.access_version) == (status, 3)


# ---- DecideMemberCommandHandler ----------------------------------------------


class World:
    def __init__(self) -> None:
        self.store = Store()
        self.clock = Clock()
        self.admin = self.add(member(S.APPROVED, Role.ADMIN, telegram_id=1))
        self.second_admin = self.add(member(S.APPROVED, Role.ADMIN, telegram_id=2))
        self.applicant = self.add(member(S.PENDING, telegram_id=3))
        self.sut = DecideMemberCommandHandler(
            lambda: FakeUnitOfWork(self.store), self.clock.now
        )

    def add(self, new: Member) -> Member:
        self.store.members[new.id] = new
        return new

    def current(self, member_id: MemberId) -> Member:
        return self.store.members[member_id]

    def session(self, owner: Member) -> AuthSession:
        session = AuthSession(
            uuid4(),
            member_id=owner.id.value,
            created_at=NOW,
            expires_at=NOW + timedelta(hours=8),
        )
        self.store.sessions[session.id] = session
        return session

    def code(self, owner: Member) -> UUID:
        code = LoginCode(
            uuid4(),
            member_id=owner.id.value,
            code_hash=uuid4().hex,
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
        self.store.codes[code.id] = CodeRow(code)
        return code.id

    async def decide(
        self,
        action: Action,
        target: Member,
        *,
        actor: Member | None = None,
        version: int | None = None,
    ) -> Result[DecisionView]:
        return await self.sut.execute(
            DecideMemberCommand(
                actor_id=(actor or self.admin).id.value,
                member_id=target.id.value,
                action=action,
                expected_version=target.access_version if version is None else version,
            )
        )

    def outbox_decisions(self) -> list[object]:
        return [e.payload["decision"] for e in self.store.outbox.values()]


@pytest.fixture
def world() -> World:
    return World()


async def test_approve_stores_the_decision_and_the_new_version(world: World) -> None:
    result = await world.decide(Action.APPROVE, world.applicant)

    assert result.is_ok and result.value.changed
    stored = world.current(world.applicant.id)
    assert (stored.status, stored.access_version) == (S.APPROVED, 2)
    assert stored.decided_by == world.admin.id.value
    assert result.value.member.status == "approved"
    assert result.value.member.access_version == 2


async def test_a_decision_stores_its_notification_in_the_same_commit(
    world: World,
) -> None:
    await world.decide(Action.APPROVE, world.applicant)

    (entry,) = world.store.outbox.values()
    assert entry.type == "member.access-decided"
    assert entry.payload["decision"] == "approved"
    assert entry.payload["telegram_id"] == world.applicant.telegram_id
    assert entry.payload["access_version"] == 2


async def test_a_block_has_no_notification(world: World) -> None:
    await world.decide(Action.BLOCK, world.applicant)

    assert world.outbox_decisions() == []


async def test_an_unknown_member_is_not_found(world: World) -> None:
    result = await world.decide(Action.APPROVE, member(S.PENDING))

    assert result.is_err and result.error.type is ErrorType.NOT_FOUND


async def test_a_repeated_decision_writes_nothing_even_from_another_admin(
    world: World,
) -> None:
    await world.decide(Action.APPROVE, world.applicant)
    version = world.current(world.applicant.id).access_version
    outbox = dict(world.store.outbox)

    again = await world.decide(
        Action.APPROVE, world.applicant, actor=world.second_admin, version=1
    )

    assert again.is_ok and not again.value.changed
    assert world.current(world.applicant.id).access_version == version
    assert world.store.outbox == outbox


async def test_block_revokes_every_session_and_cancels_live_codes(
    world: World,
) -> None:
    await world.decide(Action.APPROVE, world.applicant)
    approved = world.current(world.applicant.id)
    first, second = world.session(approved), world.session(approved)
    other = world.session(world.second_admin)
    code_id = world.code(approved)

    await world.decide(Action.BLOCK, approved)

    assert world.store.sessions[first.id].revoked_at == NOW
    assert world.store.sessions[second.id].revoked_at == NOW
    assert world.store.sessions[other.id].revoked_at is None
    assert world.store.codes[code_id].cancelled_at == NOW


async def test_unblock_restores_approval_but_not_old_sessions(world: World) -> None:
    await world.decide(Action.APPROVE, world.applicant)
    session = world.session(world.current(world.applicant.id))
    await world.decide(Action.BLOCK, world.current(world.applicant.id))

    result = await world.decide(Action.UNBLOCK, world.current(world.applicant.id))

    assert result.value.member.status == "approved"
    assert world.store.sessions[session.id].revoked_at is not None
    assert not [r for r in world.store.codes.values() if r.cancelled_at is None]


async def test_unblocked_admin_keeps_the_admin_role(world: World) -> None:
    await world.decide(Action.BLOCK, world.second_admin)

    result = await world.decide(Action.UNBLOCK, world.current(world.second_admin.id))

    assert result.value.member.role == "admin"


async def test_a_stale_version_changes_nothing(world: World) -> None:
    result = await world.decide(Action.APPROVE, world.applicant, version=9)

    assert result.is_err and result.error.code == "version_conflict"
    assert world.current(world.applicant.id).status is S.PENDING
    assert world.store.outbox == {}


async def test_self_block_is_refused_without_any_change(world: World) -> None:
    session = world.session(world.admin)

    result = await world.decide(Action.BLOCK, world.admin)

    assert result.is_err and result.error.code == "cannot_block_self"
    assert world.current(world.admin.id).status is S.APPROVED
    assert world.store.sessions[session.id].revoked_at is None


async def test_a_blocked_admin_cannot_remove_the_last_admin(world: World) -> None:
    session = world.session(world.second_admin)
    # The second admin was blocked by the first, so they lose the right to decide.
    await world.decide(Action.BLOCK, world.second_admin)

    result = await world.decide(Action.BLOCK, world.admin, actor=world.second_admin)

    assert result.is_err and result.error.code == "last_admin"
    assert world.current(world.admin.id).status is S.APPROVED
    assert world.store.sessions[session.id].revoked_at == NOW  # the first block stays


async def test_a_blocked_admin_cannot_decide_anything_else(world: World) -> None:
    await world.decide(Action.BLOCK, world.second_admin)

    result = await world.decide(
        Action.APPROVE, world.applicant, actor=world.second_admin
    )

    assert result.is_err and result.error.code == "unauthenticated"
    assert world.current(world.applicant.id).status is S.PENDING


async def test_a_blocked_admin_learns_nothing_from_a_repeat(world: World) -> None:
    await world.decide(Action.APPROVE, world.applicant)
    await world.decide(Action.BLOCK, world.second_admin)

    result = await world.decide(
        Action.APPROVE, world.applicant, actor=world.second_admin, version=1
    )

    assert result.is_err and result.error.code == "unauthenticated"


async def test_a_plain_user_cannot_decide(world: World) -> None:
    user = world.add(member(S.APPROVED, telegram_id=4))

    result = await world.decide(Action.APPROVE, world.applicant, actor=user)

    assert result.is_err and result.error.code == "admin_required"
    assert world.current(world.applicant.id).status is S.PENDING
