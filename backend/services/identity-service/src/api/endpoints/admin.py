"""Admin API: join requests, members and access decisions (browser contract)."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from api.dependencies import AdminDI, DecideMemberDI, ListMembersDI
from api.schemas import DecisionBody
from api.security import require_same_origin
from application.commands.access import DecideMemberCommand
from application.queries.members import ListMembersQuery, admin_member_view
from contracts.views import AdminMemberView, DecisionView
from domain.access import Action
from domain.entities.auth import MemberStatus

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]
RequestStatus = Annotated[
    Literal["pending", "rejected"], Query(description="pending|rejected")
]


@router.get(
    "/join-requests",
    response_model=list[AdminMemberView],
    summary="Заявки на доступ",
    description="Ожидающие (по умолчанию) или отклонённые заявки, по created_at и id.",
)
async def join_requests(
    admin: AdminDI,
    handler: ListMembersDI,
    response: Response,
    status: RequestStatus = "pending",
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[AdminMemberView]:
    """Список заявок для администратора."""
    response.headers["Cache-Control"] = "no-store"
    return await handler.execute(
        ListMembersQuery((MemberStatus(status),), limit, offset)
    )


@router.get(
    "/members",
    response_model=list[AdminMemberView],
    summary="Участники",
    description=(
        "Допущенные и заблокированные участники; параметр status выбирает любой статус."
    ),
)
async def members(
    admin: AdminDI,
    handler: ListMembersDI,
    response: Response,
    status: MemberStatus | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[AdminMemberView]:
    """Список участников для администратора."""
    response.headers["Cache-Control"] = "no-store"
    statuses = (status,) if status else (MemberStatus.APPROVED, MemberStatus.BLOCKED)
    return await handler.execute(ListMembersQuery(statuses, limit, offset))


@router.post(
    "/members/{member_id}/{action}",
    response_model=DecisionView,
    summary="Решение по заявке или участнику",
    description=(
        "approve, reject, block или unblock с проверкой expected_version; "
        "повтор уже достигнутого решения возвращает changed=false без эффекта."
    ),
    dependencies=[Depends(require_same_origin)],
)
async def decide_member(
    member_id: UUID,
    action: Action,
    body: DecisionBody,
    admin: AdminDI,
    handler: DecideMemberDI,
    response: Response,
) -> DecisionView:
    """Принять решение от имени текущего администратора."""
    result = await handler.execute(
        DecideMemberCommand(
            actor_id=admin.member.id,
            member_id=member_id,
            action=action,
            expected_version=body.expected_version,
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return DecisionView(member=admin_member_view(result.member), changed=result.changed)


__all__ = ["router"]
