"""Public routes served through the single-origin ingress."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.params import Depends as DependsParameter
from pydantic import BaseModel, Field

from api.containers import Services
from api.security import SESSION_COOKIE, Operation, require_same_origin
from application.queries.members import member_view
from contracts.views import LoginCodeView, MemberView
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


def service_operation(operation: Operation) -> DependsParameter:
    async def authorize(
        services: Annotated[Services, Depends(get_services)],
        x_service_key: Annotated[str | None, Header()] = None,
    ) -> None:
        services.service_keys.authorize(x_service_key, operation)

    return DependsParameter(authorize)


class IssueCodeRequest(BaseModel):
    telegram_id: int = Field(gt=0)


@router.get(
    "/internal/v1/members/by-telegram/{telegram_id}",
    dependencies=[service_operation(Operation.FIND_MEMBER)],
)
async def find_member(
    telegram_id: int,
    response: Response,
    services: Annotated[Services, Depends(get_services)],
) -> MemberView:
    member = await services.find_member(telegram_id)
    if member is None:
        raise HTTPException(status_code=404, detail="member_not_found")
    response.headers["Cache-Control"] = "no-store"
    return member


@router.post(
    "/internal/v1/login-codes",
    dependencies=[service_operation(Operation.ISSUE_LOGIN_CODE)],
)
async def issue_login_code(
    body: IssueCodeRequest,
    response: Response,
    services: Annotated[Services, Depends(get_services)],
) -> LoginCodeView:
    response.headers["Cache-Control"] = "no-store"
    return await services.issue_code(body.telegram_id)
