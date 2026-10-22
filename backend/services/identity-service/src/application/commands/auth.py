"""Authentication commands."""

from uuid import uuid4

from application.ports import Clock, UnitOfWorkFactory
from domain.entities.auth import Member, MemberStatus, Role


class EnsureInitialAdmin:
    """Create the first admin once; an existing member is never changed."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def __call__(self, telegram_id: int) -> bool:
        member = Member(
            id=uuid4(),
            telegram_id=telegram_id,
            role=Role.ADMIN,
            status=MemberStatus.APPROVED,
            created_at=self._clock(),
        )
        async with self._uow_factory() as uow:
            created = await uow.members.add_if_absent(member)
            await uow.commit()
        return created
