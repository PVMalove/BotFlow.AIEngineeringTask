"""Join requests: a pending member row is the request."""

from dataclasses import dataclass

from kernel_domain.result import Result

from application.ports import Clock, RateLimiter, UnitOfWorkFactory
from contracts.views import JoinRequestView, MemberView
from domain.entities.member import Member
from domain.errors import IdentityErrors
from domain.value_objects.access import JoinOutcome
from domain.value_objects.member_id import MemberId


@dataclass(frozen=True, slots=True)
class SubmitJoinRequestCommand:
    """Параметры заявки на доступ из Telegram."""

    telegram_id: int
    display_name: str | None = None


class SubmitJoinRequestCommandHandler:
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
                created = Member.request_join(
                    MemberId.new_id(),
                    telegram_id=telegram_id,
                    display_name=command.display_name,
                    now=now,
                )
                if await uow.members.add_if_absent(created):
                    await uow.commit()
                    return _joined(JoinOutcome.SUBMITTED, created)
                # A concurrent first request won; answer from its committed row.
                member = await uow.members.get_by_telegram_id(telegram_id, lock=True)
                assert member is not None
            loaded_version = member.access_version
            outcome = member.join_again(command.display_name, now)
            if outcome is JoinOutcome.SUBMITTED:  # a rejected member is reopened
                if not await uow.members.update_access(
                    member, loaded_version=loaded_version
                ):
                    raise RuntimeError("Member changed under its row lock.")
                await uow.commit()
        return _joined(outcome, member)


def _joined(outcome: JoinOutcome, member: Member) -> Result[JoinRequestView]:
    return Result[JoinRequestView].ok(
        JoinRequestView(outcome=outcome.value, member=MemberView.from_domain(member))
    )
