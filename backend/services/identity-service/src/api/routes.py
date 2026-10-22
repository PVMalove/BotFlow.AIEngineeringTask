"""Public routes served through the single-origin ingress."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from api.containers import Services
from api.security import SESSION_COOKIE, require_same_origin
from application.queries.members import member_view
from contracts.views import MemberView
from domain.entities.auth import InvalidSession

router = APIRouter()


async def get_services(request: Request) -> Services:
    services: Services = await request.app.state.container.services.async_()
    return services


class ExchangeRequest(BaseModel):
    code: str = Field(max_length=64)


@router.post("/api/v1/auth/exchange", dependencies=[Depends(require_same_origin)])
async def exchange_login_code(
    body: ExchangeRequest,
    request: Request,
    response: Response,
    services: Annotated[Services, Depends(get_services)],
) -> MemberView:
    # Behind the ingress this is the proxy address: the limit is shared and the
    # forwarded-for header is deliberately not trusted.
    client = request.client.host if request.client else "unknown"
    grant = await services.exchange(body.code, client=client)
    lifetime = grant.session.expires_at - grant.session.created_at
    services.cookies.set_session(response, grant.token, int(lifetime.total_seconds()))
    response.headers["Cache-Control"] = "no-store"
    return member_view(grant.member)


@router.get("/api/v1/users/me")
async def current_member(
    request: Request,
    response: Response,
    services: Annotated[Services, Depends(get_services)],
) -> MemberView:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise InvalidSession
    current = await services.authenticate(token)
    response.headers["Cache-Control"] = "no-store"
    return member_view(current.member)


@router.post(
    "/api/v1/auth/logout",
    status_code=204,
    dependencies=[Depends(require_same_origin)],
)
async def logout(
    request: Request, services: Annotated[Services, Depends(get_services)]
) -> Response:
    response = Response(status_code=204)
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        try:
            current = await services.authenticate(token)
        except InvalidSession:
            pass  # nothing to revoke; the cookie is cleared regardless
        else:
            await services.logout(current.session_id)
    services.cookies.clear(response)
    return response
