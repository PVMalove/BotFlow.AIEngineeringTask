"""User and member profile endpoints."""

from fastapi import APIRouter, Depends
from kernel_platform.http.cache import no_store
from kernel_platform.http.envelope import ApiResponse

from api.dependencies import RequiredAuth
from contracts.views import MemberView

router = APIRouter(
    prefix="/api/v1/users", tags=["users"], dependencies=[Depends(no_store)]
)


@router.get(
    "/me",
    response_model=ApiResponse[MemberView],
    summary="Получение профиля текущего участника",
    description=(
        "Возвращает данные текущего авторизованного участника по сессионной cookie."
    ),
)
async def current_member(current: RequiredAuth) -> ApiResponse[MemberView]:
    """Получить профиль текущего участника из активной сессии."""
    return ApiResponse(data=MemberView.from_domain(current.member))


__all__ = ["router"]
