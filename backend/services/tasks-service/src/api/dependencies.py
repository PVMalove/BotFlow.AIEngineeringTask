"""FastAPI dependency wiring: services, authentication and use-case handlers."""

from typing import Annotated

from fastapi import Depends, Request
from kernel_domain.result import Result
from kernel_platform.http.match import unwrap
from kernel_platform.security.actor import Actor
from kernel_platform.security.errors import AuthErrors

from api.containers import Services
from application.commands.change_task_status import ChangeTaskStatusCommandHandler
from application.queries.get_task import GetTaskQueryHandler
from application.queries.list_tasks import ListTasksQueryHandler
from infrastructure.identity_gateway import SESSION_COOKIE


async def get_services(request: Request) -> Services:
    services: Services = await request.app.state.container.services.async_()
    return services


ServicesDI = Annotated[Services, Depends(get_services)]


async def get_actor(request: Request, services: ServicesDI) -> Actor:
    """The approved member of the session; checked on every request, because
    access can be revoked."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return unwrap(Result[Actor].fail(AuthErrors.unauthenticated()))
    return unwrap(await services.identity.require_approved(token))


RequiredAuth = Annotated[Actor, Depends(get_actor)]


def get_list_tasks_handler(services: ServicesDI) -> ListTasksQueryHandler:
    return services.list_tasks


def get_task_handler(services: ServicesDI) -> GetTaskQueryHandler:
    return services.get_task


def get_change_task_status_handler(
    services: ServicesDI,
) -> ChangeTaskStatusCommandHandler:
    return services.change_task_status


ListTasksDI = Annotated[ListTasksQueryHandler, Depends(get_list_tasks_handler)]
GetTaskDI = Annotated[GetTaskQueryHandler, Depends(get_task_handler)]
ChangeTaskStatusDI = Annotated[
    ChangeTaskStatusCommandHandler, Depends(get_change_task_status_handler)
]
