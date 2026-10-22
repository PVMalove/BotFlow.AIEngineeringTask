"""User and member profile endpoints."""

from fastapi import APIRouter, Response
from kernel_platform.http import ApiResponse

from api.dependencies import RequiredAuth
from contracts.views import MemberView

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get(
    "/me",
    response_model=ApiResponse[MemberView],
    summary="Получение профиля текущего участника",
    description=(
        "Возвращает данные текущего авторизованного участника по сессионной cookie."
    ),
)
async def current_member(
    current: RequiredAuth, response: Response
) -> ApiResponse[MemberView]:
    """Получить профиль текущего участника из активной сессии."""
    response.headers["Cache-Control"] = "no-store"
    return ApiResponse(data=MemberView.from_domain(current.member))


__all__ = ["router"]
