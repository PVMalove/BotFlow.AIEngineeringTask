"""User and member profile endpoints."""

from fastapi import APIRouter, Request, Response

from api.dependencies import AuthenticateSessionDI
from api.security import SESSION_COOKIE
from application.queries.members import AuthenticateSessionQuery, member_view
from contracts.views import MemberView
from domain.entities.auth import InvalidSession

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get(
    "/me",
    response_model=MemberView,
    summary="Получение профиля текущего участника",
    description=(
        "Возвращает данные текущего авторизованного участника по сессионной cookie."
    ),
)
async def current_member(
    request: Request,
    response: Response,
    handler: AuthenticateSessionDI,
) -> MemberView:
    """Получить профиль текущего участника из активной сессии."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise InvalidSession
    current = await handler.execute(AuthenticateSessionQuery(token=token))
    response.headers["Cache-Control"] = "no-store"
    return member_view(current.member)


__all__ = ["router"]
