"""Decision table and the DecideMember use case against in-memory ports."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from conftest import NOW, Clock, FakeUnitOfWork, Store

from application.commands.access import (
    DecideMember,
    DecideMemberCommand,
    DecisionResult,
)
from domain.access import (
    Action,
    AdminRequired,
    CannotBlockSelf,
    Decision,
    InvalidTransition,
    LastAdmin,
    MemberNotFound,
    VersionConflict,
    decide,
)
from domain.entities.auth import (
    AuthSession,
    InvalidSession,
    LoginCode,
    Member,
    MemberStatus,
    Role,
)

S = MemberStatus


def member(
    status: MemberStatus, role: Role = Role.USER, version: int = 1, telegram_id: int = 0
) -> Member:
    return Member(
        id=uuid4(),
        telegram_id=telegram_id or uuid4().int % 10**9,
        role=role,
        status=status,
        created_at=NOW,
        access_version=version,
    )


def apply(target: Member, action: Action, **overrides: object) -> Decision:
    values = dict(
        actor_id=uuid4(),
        approved_admins=2,
        expected_version=target.access_version,
        now=NOW + timedelta(minutes=1),
    )
    values.update(overrides)
    return decide(target, action, **values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("action", "before", "after"),
    [
        (Action.APPROVE, S.PENDING, S.APPROVED),
        (Action.REJECT, S.PENDING, S.REJECTED),
        (Action.BLOCK, S.PENDING, S.BLOCKED),
        (Action.BLOCK, S.REJECTED, S.BLOCKED),
        (Action.BLOCK, S.APPROVED, S.BLOCKED),
        (Action.UNBLOCK, S.BLOCKED, S.APPROVED),
    ],
)
def test_allowed_transitions_bump_the_version_and_record_the_decision(
    action: Action, before: MemberStatus, after: MemberStatus
) -> None:
    target = member(before, version=4)
    actor = uuid4()

    decision = apply(target, action, actor_id=actor)

    assert decision.changed
    assert decision.member.status is after
    assert decision.member.access_version == 5
    assert decision.member.decided_by == actor
    assert decision.member.status_changed_at == NOW + timedelta(minutes=1)
    assert decision.member.id == target.id


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

    decision = apply(target, action, expected_version=1)

    assert not decision.changed
    assert decision.member == target


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
    with pytest.raises(InvalidTransition):
        apply(member(before), action)


def test_a_stale_version_is_a_conflict() -> None:
    with pytest.raises(VersionConflict):
        apply(member(S.PENDING, version=2), Action.APPROVE, expected_version=1)


def test_an_admin_cannot_block_themselves() -> None:
    admin = member(S.APPROVED, Role.ADMIN)

    with pytest.raises(CannotBlockSelf):
        apply(admin, Action.BLOCK, actor_id=admin.id)


def test_the_last_approved_admin_cannot_be_blocked() -> None:
    with pytest.raises(LastAdmin):
        apply(member(S.APPROVED, Role.ADMIN), Action.BLOCK, approved_admins=1)


def test_a_non_admin_or_unapproved_admin_is_not_protected_by_the_guard() -> None:
    assert apply(member(S.APPROVED), Action.BLOCK, approved_admins=1).changed
    assert apply(member(S.PENDING, Role.ADMIN), Action.BLOCK, approved_admins=1).changed


# ---- DecideMember ----------------------------------------------------------


class World:
    def __init__(self) -> None:
        self.store = Store()
        self.clock = Clock()
        self.admin = self.add(member(S.APPROVED, Role.ADMIN, telegram_id=1))
        self.second_admin = self.add(member(S.APPROVED, Role.ADMIN, telegram_id=2))
        self.applicant = self.add(member(S.PENDING, telegram_id=3))
        self.sut = DecideMember(lambda: FakeUnitOfWork(self.store), self.clock)

    def add(self, new: Member) -> Member:
        self.store.members[new.id] = new
        return new

    def current(self, member_id: UUID) -> Member:
        return self.store.members[member_id]

    def session(self, owner: Member) -> AuthSession:
        session = AuthSession(uuid4(), owner.id, NOW, NOW + timedelta(hours=8))
        self.store.sessions[session.id] = session
        return session

    def code(self, owner: Member) -> UUID:
        code = LoginCode(
            uuid4(), owner.id, uuid4().hex, NOW, NOW + timedelta(minutes=5)
        )
        from conftest import CodeRow

        self.store.codes[code.id] = CodeRow(code)
        return code.id

    async def decide(
        self,
        action: Action,
        target: Member,
        *,
        actor: Member | None = None,
        version: int | None = None,
    ) -> DecisionResult:
        return await self.sut.execute(
            DecideMemberCommand(
                actor_id=(actor or self.admin).id,
                member_id=target.id,
                action=action,
                expected_version=target.access_version if version is None else version,
            )
        )


@pytest.fixture
def world() -> World:
    return World()


async def test_approve_stores_the_decision_and_the_new_version(world: World) -> None:
    result = await world.decide(Action.APPROVE, world.applicant)

    assert result.changed
    stored = world.current(world.applicant.id)
    assert stored == result.member
    assert (stored.status, stored.access_version) == (S.APPROVED, 2)
    assert stored.decided_by == world.admin.id


async def test_an_unknown_member_is_not_found(world: World) -> None:
    ghost = member(S.PENDING)

    with pytest.raises(MemberNotFound):
        await world.decide(Action.APPROVE, ghost)


async def test_a_repeated_decision_writes_nothing_even_from_another_admin(
    world: World,
) -> None:
    await world.decide(Action.APPROVE, world.applicant)
    snapshot = dict(world.store.members)

    again = await world.decide(
        Action.APPROVE, world.applicant, actor=world.second_admin, version=1
    )

    assert not again.changed
    assert world.store.members == snapshot


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

    assert result.member.status is S.APPROVED
    assert world.store.sessions[session.id].revoked_at is not None
    assert not [r for r in world.store.codes.values() if r.cancelled_at is None]


async def test_unblocked_admin_keeps_the_admin_role(world: World) -> None:
    await world.decide(Action.BLOCK, world.second_admin)

    result = await world.decide(Action.UNBLOCK, world.current(world.second_admin.id))

    assert result.member.role is Role.ADMIN


async def test_a_stale_version_changes_nothing(world: World) -> None:
    with pytest.raises(VersionConflict):
        await world.decide(Action.APPROVE, world.applicant, version=9)

    assert world.current(world.applicant.id) == world.applicant


async def test_self_block_is_refused_without_any_change(world: World) -> None:
    session = world.session(world.admin)

    with pytest.raises(CannotBlockSelf):
        await world.decide(Action.BLOCK, world.admin)

    assert world.current(world.admin.id) == world.admin
    assert world.store.sessions[session.id].revoked_at is None


async def test_a_blocked_admin_cannot_remove_the_last_admin(
    world: World,
) -> None:
    session = world.session(world.second_admin)
    # The second admin was blocked by the first, so they lose the right to decide.
    await world.decide(Action.BLOCK, world.second_admin)

    with pytest.raises(LastAdmin):
        await world.decide(Action.BLOCK, world.admin, actor=world.second_admin)

    assert world.current(world.admin.id).status is S.APPROVED
    assert world.store.sessions[session.id].revoked_at == NOW  # the first block stays


async def test_a_blocked_admin_cannot_decide_anything_else(world: World) -> None:
    await world.decide(Action.BLOCK, world.second_admin)

    with pytest.raises(InvalidSession):
        await world.decide(Action.APPROVE, world.applicant, actor=world.second_admin)

    assert world.current(world.applicant.id).status is S.PENDING


async def test_a_blocked_admin_learns_nothing_from_a_repeat(world: World) -> None:
    await world.decide(Action.APPROVE, world.applicant)
    await world.decide(Action.BLOCK, world.second_admin)

    with pytest.raises(InvalidSession):
        await world.decide(
            Action.APPROVE, world.applicant, actor=world.second_admin, version=1
        )


async def test_a_plain_user_cannot_decide(world: World) -> None:
    user = world.add(replace(member(S.APPROVED, telegram_id=4)))

    with pytest.raises(AdminRequired):
        await world.decide(Action.APPROVE, world.applicant, actor=user)

    assert world.current(world.applicant.id).status is S.PENDING
