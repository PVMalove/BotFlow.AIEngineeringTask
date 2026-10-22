"""Board endpoints: list with keyset cursor, and one task card."""

from typing import Annotated
from uuid import UUID

from botflow_platform.http import ApiResponse
from fastapi import APIRouter, Depends, Query, Request, Response

from api.dependencies import ServicesDI
from api.security import require_same_origin
from application.queries.tasks import DEFAULT_LIMIT, MAX_LIMIT
from contracts.views import ChangeStatusRequest, TaskView, task_view
from domain.tasks import TaskStatus
from infrastructure.identity_gateway import SESSION_COOKIE

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
    request: Request,
    response: Response,
    services: ServicesDI,
    status: TaskStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> ApiResponse[list[TaskView]]:
    page = await services.list_tasks.execute(
        request.cookies.get(SESSION_COOKIE), status=status, limit=limit, cursor=cursor
    )
    response.headers["Cache-Control"] = "no-store"
    return ApiResponse(
        data=[task_view(task) for task in page.items],
        meta={"next_cursor": page.next_cursor, "has_more": page.has_more},
    )


@router.get(
    "/{task_id}",
    response_model=ApiResponse[TaskView],
    summary="Карточка задачи",
)
async def get_task(
    task_id: UUID, request: Request, response: Response, services: ServicesDI
) -> ApiResponse[TaskView]:
    task = await services.get_task.execute(request.cookies.get(SESSION_COOKIE), task_id)
    response.headers["Cache-Control"] = "no-store"
    return ApiResponse(data=task_view(task))


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
    body: ChangeStatusRequest,
    request: Request,
    response: Response,
    services: ServicesDI,
) -> ApiResponse[TaskView]:
    task = await services.change_task_status.execute(
        request.cookies.get(SESSION_COOKIE),
        task_id,
        body.status,
        body.expected_version,
    )
    response.headers["Cache-Control"] = "no-store"
    return ApiResponse(data=task_view(task))
