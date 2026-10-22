"""Admin API: join requests, members and access decisions (browser contract)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from kernel_platform.http import ApiResponse, match_result
from kernel_platform.security import require_same_origin

from api.dependencies import AdminAuth, DecideMemberDI, ListMembersDI
from api.schemas import DecisionBody, JoinRequestsListRequest, MembersListRequest
from contracts.views import AdminMemberView, DecisionView
from domain.access import Action

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get(
    "/join-requests",
    response_model=ApiResponse[list[AdminMemberView]],
    summary="Заявки на доступ",
    description="Ожидающие (по умолчанию) или отклонённые заявки, по created_at и id.",
)
async def join_requests(
    request: Annotated[JoinRequestsListRequest, Query()],
    _: AdminAuth,
    handler: ListMembersDI,
    response: Response,
) -> ApiResponse[list[AdminMemberView]]:
    """Список заявок для администратора."""
    response.headers["Cache-Control"] = "no-store"
    return match_result(await handler.execute(request.to_query()))


@router.get(
    "/members",
    response_model=ApiResponse[list[AdminMemberView]],
    summary="Участники",
    description=(
        "Допущенные и заблокированные участники; параметр status выбирает любой статус."
    ),
)
async def members(
    request: Annotated[MembersListRequest, Query()],
    _: AdminAuth,
    handler: ListMembersDI,
    response: Response,
) -> ApiResponse[list[AdminMemberView]]:
    """Список участников для администратора."""
    response.headers["Cache-Control"] = "no-store"
    return match_result(await handler.execute(request.to_query()))


@router.post(
    "/members/{member_id}/{action}",
    response_model=ApiResponse[DecisionView],
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
    admin: AdminAuth,
    handler: DecideMemberDI,
    response: Response,
) -> ApiResponse[DecisionView]:
    """Принять решение от имени текущего администратора."""
    response.headers["Cache-Control"] = "no-store"
    return match_result(
        await handler.execute(body.to_command(admin.member.id, member_id, action))
    )


__all__ = ["router"]
