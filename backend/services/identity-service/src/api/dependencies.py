"""FastAPI dependency injection wiring for identity service."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from fastapi.params import Depends as DependsParameter

from api.containers import Services
from api.security import SESSION_COOKIE, Operation
from application.commands.access import DecideMember
from application.commands.auth import (
    ExchangeLoginCode,
    IssueLoginCode,
    Logout,
)
from application.commands.join import SubmitJoinRequest
from application.queries.members import (
    AuthenticateSession,
    AuthenticateSessionQuery,
    CurrentSession,
    FindMemberByTelegram,
    ListMembers,
)
from domain.entities.auth import InvalidSession, Role


async def get_services(request: Request) -> Services:
    services: Services = await request.app.state.container.services.async_()
    return services


ServicesDI = Annotated[Services, Depends(get_services)]


def get_exchange_handler(services: ServicesDI) -> ExchangeLoginCode:
    return services.exchange


ExchangeLoginCodeDI = Annotated[ExchangeLoginCode, Depends(get_exchange_handler)]


def get_logout_handler(services: ServicesDI) -> Logout:
    return services.logout


LogoutDI = Annotated[Logout, Depends(get_logout_handler)]


def get_authenticate_handler(services: ServicesDI) -> AuthenticateSession:
    return services.authenticate


AuthenticateSessionDI = Annotated[
    AuthenticateSession, Depends(get_authenticate_handler)
]


def get_find_member_handler(services: ServicesDI) -> FindMemberByTelegram:
    return services.find_member


FindMemberDI = Annotated[FindMemberByTelegram, Depends(get_find_member_handler)]


def get_issue_code_handler(services: ServicesDI) -> IssueLoginCode:
    return services.issue_code


IssueLoginCodeDI = Annotated[IssueLoginCode, Depends(get_issue_code_handler)]


def get_submit_join_handler(services: ServicesDI) -> SubmitJoinRequest:
    return services.submit_join


SubmitJoinRequestDI = Annotated[SubmitJoinRequest, Depends(get_submit_join_handler)]


def get_decide_handler(services: ServicesDI) -> DecideMember:
    return services.decide


DecideMemberDI = Annotated[DecideMember, Depends(get_decide_handler)]


def get_list_members_handler(services: ServicesDI) -> ListMembers:
    return services.list_members


ListMembersDI = Annotated[ListMembers, Depends(get_list_members_handler)]


async def require_admin(
    request: Request, handler: AuthenticateSessionDI
) -> CurrentSession:
    """Cookie session of an approved admin; access is re-read on every request."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise InvalidSession
    current = await handler.execute(AuthenticateSessionQuery(token=token))
    if current.member.role is not Role.ADMIN:
        raise HTTPException(status_code=403, detail="admin_required")
    return current


AdminDI = Annotated[CurrentSession, Depends(require_admin)]


def service_operation(operation: Operation) -> DependsParameter:
    async def authorize(
        services: ServicesDI,
        x_service_key: Annotated[str | None, Header()] = None,
    ) -> None:
        services.service_keys.authorize(x_service_key, operation)

    return DependsParameter(authorize)


__all__ = [
    "AdminDI",
    "AuthenticateSessionDI",
    "DecideMemberDI",
    "ListMembersDI",
    "ExchangeLoginCodeDI",
    "FindMemberDI",
    "IssueLoginCodeDI",
    "LogoutDI",
    "ServicesDI",
    "SubmitJoinRequestDI",
    "get_authenticate_handler",
    "get_exchange_handler",
    "get_find_member_handler",
    "get_issue_code_handler",
    "get_logout_handler",
    "get_services",
    "require_admin",
    "get_submit_join_handler",
    "service_operation",
]
