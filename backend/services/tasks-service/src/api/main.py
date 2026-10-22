import re
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from kernel_platform.http import create_app
from sqlalchemy.exc import DBAPIError, TimeoutError

from api.containers import Container
from api.endpoints.tasks import router as tasks_router
from application.ports import (
    Forbidden,
    IdentityUnavailable,
    InvalidCursor,
    NotApproved,
    TaskNotFound,
    Unauthenticated,
    VersionConflict,
)

# Every rejection carries a detail code, a message for the user and a request id for
# support. Deliberately coarse: a caller learns why only as far as it can act on it.
_REJECTIONS: dict[type[Exception], tuple[int, str, str]] = {
    Unauthenticated: (401, "unauthenticated", "Войдите заново."),
    NotApproved: (403, "not_approved", "Доступ к доске не подтверждён."),
    InvalidCursor: (400, "invalid_cursor", "Страница списка недействительна."),
    TaskNotFound: (404, "task_not_found", "Задача не найдена."),
    # A status change is refused: the client re-reads the card, never retries blindly.
    Forbidden: (403, "forbidden", "Менять статус этой задачи может только её автор."),
    VersionConflict: (
        409,
        "version_conflict",
        "Задача уже изменилась. Обновите карточку и повторите действие.",
    ),
}
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


def _problem(
    request: Request,
    status: int,
    detail: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    sent = request.headers.get("x-request-id", "")
    # Echo only a well-formed id: the header value reaches logs and the response.
    request_id = sent if _REQUEST_ID.fullmatch(sent) else uuid4().hex
    return JSONResponse(
        {"detail": detail, "message": message, "request_id": request_id},
        status_code=status,
        headers={"X-Request-ID": request_id, **(headers or {})},
    )


async def reject(request: Request, error: Exception) -> JSONResponse:
    return _problem(request, *_REJECTIONS[type(error)])


def _unavailable(detail: str) -> Any:
    async def handler(request: Request, error: Exception) -> JSONResponse:
        return _problem(
            request,
            503,
            detail,
            "Сервис временно недоступен. Повторите позже.",
            {"Retry-After": "5"},
        )

    return handler


def create_tasks_app(container: Any = None) -> FastAPI:
    app = create_app("tasks", container)
    app.state.container = container
    app.include_router(tasks_router)
    for error in _REJECTIONS:
        app.add_exception_handler(error, reject)
    # Access is never assumed when Identity cannot answer.
    app.add_exception_handler(IdentityUnavailable, _unavailable("identity_unavailable"))
    for storage_error in (DBAPIError, TimeoutError, OSError):
        app.add_exception_handler(storage_error, _unavailable("tasks_unavailable"))
    return app


app = create_tasks_app(Container())
