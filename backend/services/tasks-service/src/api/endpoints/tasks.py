"""Board endpoints: thin adapters from HTTP to the task commands and queries."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from kernel_platform.http.envelope import ApiResponse
from kernel_platform.http.match import match_page, match_result
from kernel_platform.security.origin import require_same_origin

from api.dependencies import ChangeTaskStatusDI, GetTaskDI, ListTasksDI, RequiredAuth
from api.schemas import ChangeStatusBody, TaskListRequest
from application.queries.get_task import GetTaskQuery
from contracts.task import TaskView

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.get(
    "",
    response_model=ApiResponse[list[TaskView]],
    summary="Список задач общей доски",
    description=(
        "Задачи от новых к старым. Курсор непрозрачен: передайте meta.next_cursor "
        "как cursor. Доступно любому допущенному участнику."
    ),
)
async def list_tasks(
    request: Annotated[TaskListRequest, Query()],
    response: Response,
    _: RequiredAuth,
    handler: ListTasksDI,
) -> ApiResponse[list[TaskView]]:
    response.headers["Cache-Control"] = "no-store"
    return match_page(await handler.execute(request.to_query()))


@router.get(
    "/{task_id}",
    response_model=ApiResponse[TaskView],
    summary="Карточка задачи",
)
async def get_task(
    task_id: UUID, response: Response, _: RequiredAuth, handler: GetTaskDI
) -> ApiResponse[TaskView]:
    response.headers["Cache-Control"] = "no-store"
    return match_result(await handler.execute(GetTaskQuery(task_id)))


@router.patch(
    "/{task_id}",
    response_model=ApiResponse[TaskView],
    dependencies=[Depends(require_same_origin)],
    summary="Сменить статус задачи",
    description=(
        "Автор или администратор переводит задачу в любой статус. "
        "expected_version — версия карточки, которую видел клиент: устаревшая даёт "
        "409, повтор с актуальной версией и тем же статусом — 200 без изменений. "
        "При 409 перечитайте карточку."
    ),
)
async def change_task_status(
    task_id: UUID,
    body: ChangeStatusBody,
    response: Response,
    actor: RequiredAuth,
    handler: ChangeTaskStatusDI,
) -> ApiResponse[TaskView]:
    response.headers["Cache-Control"] = "no-store"
    return match_result(await handler.execute(body.to_command(task_id, actor)))
