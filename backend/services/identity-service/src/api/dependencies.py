"""FastAPI dependency wiring: services, authentication and use-case handlers."""

from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.params import Depends as DependsParameter
from kernel_platform.http.match import raise_error, unwrap

from api.containers import Services
from api.security import SESSION_COOKIE, Operation
from application.commands.access import DecideMemberCommandHandler
from application.commands.auth import (
    ExchangeLoginCodeCommandHandler,
    IssueLoginCodeCommandHandler,
    LogoutCommandHandler,
)
from application.commands.join import SubmitJoinRequestCommandHandler
from application.queries.members import (
    AuthenticateSessionQuery,
    AuthenticateSessionQueryHandler,
    CurrentSession,
    FindMemberByTelegramQueryHandler,
    ListMembersQueryHandler,
)
from domain.errors import IdentityErrors
from domain.value_objects.role import Role


async def get_services(request: Request) -> Services:
    services: Services = await request.app.state.container.services.async_()
    return services


ServicesDI = Annotated[Services, Depends(get_services)]


def get_exchange_handler(services: ServicesDI) -> ExchangeLoginCodeCommandHandler:
    return services.exchange


def get_logout_handler(services: ServicesDI) -> LogoutCommandHandler:
    return services.logout


def get_authenticate_handler(services: ServicesDI) -> AuthenticateSessionQueryHandler:
    return services.authenticate


def get_find_member_handler(services: ServicesDI) -> FindMemberByTelegramQueryHandler:
    return services.find_member


def get_issue_code_handler(services: ServicesDI) -> IssueLoginCodeCommandHandler:
    return services.issue_code


def get_submit_join_handler(services: ServicesDI) -> SubmitJoinRequestCommandHandler:
    return services.submit_join


def get_decide_handler(services: ServicesDI) -> DecideMemberCommandHandler:
    return services.decide


def get_list_members_handler(services: ServicesDI) -> ListMembersQueryHandler:
    return services.list_members


ExchangeLoginCodeDI = Annotated[
    ExchangeLoginCodeCommandHandler, Depends(get_exchange_handler)
]
LogoutDI = Annotated[LogoutCommandHandler, Depends(get_logout_handler)]
AuthenticateSessionDI = Annotated[
    AuthenticateSessionQueryHandler, Depends(get_authenticate_handler)
]
FindMemberDI = Annotated[
    FindMemberByTelegramQueryHandler, Depends(get_find_member_handler)
]
IssueLoginCodeDI = Annotated[
    IssueLoginCodeCommandHandler, Depends(get_issue_code_handler)
]
SubmitJoinRequestDI = Annotated[
    SubmitJoinRequestCommandHandler, Depends(get_submit_join_handler)
]
DecideMemberDI = Annotated[DecideMemberCommandHandler, Depends(get_decide_handler)]
ListMembersDI = Annotated[ListMembersQueryHandler, Depends(get_list_members_handler)]


async def get_session(
    request: Request, handler: AuthenticateSessionDI
) -> CurrentSession:
    """Cookie session of an approved member; access is re-read on every request."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise_error(IdentityErrors.invalid_session())
    return unwrap(await handler.execute(AuthenticateSessionQuery(token=token)))


RequiredAuth = Annotated[CurrentSession, Depends(get_session)]


async def require_admin(current: RequiredAuth) -> CurrentSession:
    if current.member.role is not Role.ADMIN:
        raise_error(IdentityErrors.admin_required())
    return current


AdminAuth = Annotated[CurrentSession, Depends(require_admin)]


def service_operation(operation: Operation) -> DependsParameter:
    async def authorize(
        services: ServicesDI,
        x_service_key: Annotated[str | None, Header()] = None,
    ) -> None:
        unwrap(services.service_keys.authorize(x_service_key, operation))

    return DependsParameter(authorize)
