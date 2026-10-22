"""Access decisions: one transaction under a global decision lock."""

from dataclasses import dataclass
from uuid import UUID

from application.ports import Clock, UnitOfWorkFactory
from domain.access import (
    Action,
    AdminRequired,
    MemberNotFound,
    access_decided,
    decide,
)
from domain.entities.auth import InvalidSession, Member, MemberStatus, Role


@dataclass(frozen=True, slots=True)
class DecideMemberCommand:
    """Решение администратора по заявке или участнику."""

    actor_id: UUID
    member_id: UUID
    action: Action
    expected_version: int


@dataclass(frozen=True, slots=True)
class DecisionResult:
    member: Member
    changed: bool


class DecideMember:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: DecideMemberCommand) -> DecisionResult:
        now = self._clock()
        async with self._uow_factory() as uow:
            # One global lock: decisions are rare, and serializing them makes the
            # last-admin and self-block guards plain reads. Lock order is always
            # decision lock -> member rows by caller order; other paths take one row.
            await uow.members.lock_decisions()
            target = await uow.members.get(command.member_id, lock=True)
            if target is None:
                raise MemberNotFound
            actor = (
                target
                if command.actor_id == target.id
                else await uow.members.get(command.actor_id, lock=True)
            )
            decision = decide(
                target,
                command.action,
                actor_id=command.actor_id,
                approved_admins=await uow.members.count_approved_admins(),
                expected_version=command.expected_version,
                now=now,
            )
            # Checked after the invariants on purpose: an admin who lost access
            # in a concurrent decision still cannot remove the last admin, and
            # gets no effect or confirmation for anything else.
            if actor is None or actor.status is not MemberStatus.APPROVED:
                raise InvalidSession
            if actor.role is not Role.ADMIN:
                raise AdminRequired
            if decision.changed:
                updated = await uow.members.update_access(
                    decision.member, expected_version=target.access_version
                )
                if not updated:
                    raise RuntimeError("Member changed under its row lock.")
                if decision.member.status is MemberStatus.BLOCKED:
                    # Same transaction as the status: no session outlives the block.
                    await uow.sessions.revoke_all(target.id, now)
                    await uow.login_codes.cancel_active(target.id, now)
                intent = access_decided(decision.member, command.action, now)
                if intent is not None:
                    # Same transaction: no decision without its notification intent.
                    await uow.outbox.add(intent)
            await uow.commit()
        return DecisionResult(member=decision.member, changed=decision.changed)
