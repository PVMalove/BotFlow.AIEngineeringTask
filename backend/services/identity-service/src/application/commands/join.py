"""Join requests: a pending member row is the request."""

from dataclasses import dataclass, replace
from uuid import uuid4

from kernel_domain.result import Result

from application.ports import Clock, RateLimiter, UnitOfWorkFactory
from contracts.views import JoinRequestView, MemberView
from domain.access import JoinOutcome
from domain.entities.auth import Member, MemberStatus, Role
from domain.errors import IdentityErrors


@dataclass(frozen=True, slots=True)
class SubmitJoinRequestCommand:
    """Параметры заявки на доступ из Telegram."""

    telegram_id: int
    display_name: str | None = None


def _joined(outcome: JoinOutcome, member: Member) -> Result[JoinRequestView]:
    return Result[JoinRequestView].ok(
        JoinRequestView(outcome=outcome.value, member=MemberView.from_domain(member))
    )


class SubmitJoinRequest:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        limiter: RateLimiter,
        clock: Clock,
        attempts_per_minute: int = 5,
    ) -> None:
        self._uow_factory = uow_factory
        self._limiter = limiter
        self._clock = clock
        self._attempts = attempts_per_minute

    async def execute(
        self, command: SubmitJoinRequestCommand
    ) -> Result[JoinRequestView]:
        telegram_id = command.telegram_id
        if not await self._limiter.allow(f"join:{telegram_id}", self._attempts, 60):
            return Result[JoinRequestView].fail(IdentityErrors.rate_limited())
        now = self._clock()
        async with self._uow_factory() as uow:
            member = await uow.members.get_by_telegram_id(telegram_id, lock=True)
            if member is None:
                created = Member(
                    id=uuid4(),
                    telegram_id=telegram_id,
                    role=Role.USER,
                    status=MemberStatus.PENDING,
                    created_at=now,
                    display_name=command.display_name,
                    status_changed_at=now,
                )
                if await uow.members.add_if_absent(created):
                    await uow.commit()
                    return _joined(JoinOutcome.SUBMITTED, created)
                # A concurrent first request won; answer from its committed row.
                member = await uow.members.get_by_telegram_id(telegram_id, lock=True)
                assert member is not None
            if member.status is MemberStatus.REJECTED:
                reopened = replace(
                    member,
                    status=MemberStatus.PENDING,
                    display_name=command.display_name or member.display_name,
                    access_version=member.access_version + 1,
                    status_changed_at=now,
                    decided_by=None,
                )
                if not await uow.members.update_access(
                    reopened, expected_version=member.access_version
                ):
                    raise RuntimeError("Member changed under its row lock.")
                await uow.commit()
                return _joined(JoinOutcome.SUBMITTED, reopened)
        outcome = {
            MemberStatus.PENDING: JoinOutcome.ALREADY_PENDING,
            MemberStatus.APPROVED: JoinOutcome.ALREADY_MEMBER,
            MemberStatus.BLOCKED: JoinOutcome.BLOCKED,
        }[member.status]
        return _joined(outcome, member)
