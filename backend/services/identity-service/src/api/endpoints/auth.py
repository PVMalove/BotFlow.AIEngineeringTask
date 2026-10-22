"""Authentication endpoints for identity service."""

from fastapi import APIRouter, Depends, Request, Response
from kernel_platform.http import ApiResponse, unwrap
from kernel_platform.security import require_same_origin

from api.dependencies import (
    AuthenticateSessionDI,
    ExchangeLoginCodeDI,
    LogoutDI,
    ServicesDI,
)
from api.schemas import ExchangeRequest
from api.security import SESSION_COOKIE
from application.commands.auth import LogoutCommand
from application.queries.members import AuthenticateSessionQuery
from contracts.views import MemberView

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post(
    "/exchange",
    response_model=ApiResponse[MemberView],
    summary="Обмен одноразового кода Telegram на сессионную cookie",
    description=(
        "Проверяет одноразовый 32-символьный код входа, полученный в Telegram-боте, "
        "и выпускает активную сессию с защищённой HttpOnly cookie."
    ),
    dependencies=[Depends(require_same_origin)],
)
async def exchange_login_code(
    body: ExchangeRequest,
    request: Request,
    response: Response,
    handler: ExchangeLoginCodeDI,
    services: ServicesDI,
) -> ApiResponse[MemberView]:
    """Обменять валидный одноразовый код авторизации Telegram на сессию."""
    # Behind the ingress this is the proxy address: the limit is shared and the
    # forwarded-for header is deliberately not trusted.
    client = request.client.host if request.client else "unknown"
    grant = unwrap(await handler.execute(body.to_command(client=client)))
    lifetime = grant.session.expires_at - grant.session.created_at
    services.cookies.set_session(response, grant.token, int(lifetime.total_seconds()))
    response.headers["Cache-Control"] = "no-store"
    return ApiResponse(data=MemberView.from_domain(grant.member))


@router.post(
    "/logout",
    status_code=204,
    summary="Завершение текущей сессии",
    description="Отзывает текущую активную сессию в хранилище и очищает cookie.",
    dependencies=[Depends(require_same_origin)],
)
async def logout(
    request: Request,
    auth_handler: AuthenticateSessionDI,
    logout_handler: LogoutDI,
    services: ServicesDI,
) -> Response:
    """Отозвать сессию пользователя и удалить сессионную cookie."""
    response = Response(status_code=204)
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        current = await auth_handler.execute(AuthenticateSessionQuery(token=token))
        if current.is_ok:  # otherwise nothing to revoke; the cookie is cleared anyway
            await logout_handler.execute(
                LogoutCommand(session_id=current.value.session_id)
            )
    services.cookies.clear(response)
    return response


__all__ = ["router"]
