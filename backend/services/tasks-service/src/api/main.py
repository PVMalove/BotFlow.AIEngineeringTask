import re
from typing import Any
from uuid import uuid4

from botflow_platform.http import create_app
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
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

# Deliberately coarse: a caller learns why only as far as it can act on it.
_REJECTIONS: dict[type[Exception], tuple[int, str]] = {
    Unauthenticated: (401, "unauthenticated"),
    NotApproved: (403, "not_approved"),
    InvalidCursor: (400, "invalid_cursor"),
    TaskNotFound: (404, "task_not_found"),
}

# Rejections of a status change carry a message for the user and a request id for
# support; the client reacts by re-reading the card, never by retrying blindly.
_CHANGE_REJECTIONS: dict[type[Exception], tuple[int, str, str]] = {
    Forbidden: (403, "forbidden", "Менять статус этой задачи может только её автор."),
    VersionConflict: (
        409,
        "version_conflict",
        "Задача уже изменилась. Обновите карточку и повторите действие.",
    ),
}
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


async def reject(request: Request, error: Exception) -> JSONResponse:
    status, detail = _REJECTIONS[type(error)]
    return JSONResponse({"detail": detail}, status_code=status)


async def reject_change(request: Request, error: Exception) -> JSONResponse:
    status, detail, message = _CHANGE_REJECTIONS[type(error)]
    sent = request.headers.get("x-request-id", "")
    # Echo only a well-formed id: the header value reaches logs and the response.
    request_id = sent if _REQUEST_ID.fullmatch(sent) else uuid4().hex
    return JSONResponse(
        {"detail": detail, "message": message, "request_id": request_id},
        status_code=status,
        headers={"X-Request-ID": request_id},
    )


def _unavailable(detail: str) -> Any:
    async def handler(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(
            {"detail": detail}, status_code=503, headers={"Retry-After": "5"}
        )

    return handler


def create_tasks_app(container: Any = None) -> FastAPI:
    app = create_app("tasks", container)
    app.state.container = container
    app.include_router(tasks_router)
    for error in _REJECTIONS:
        app.add_exception_handler(error, reject)
    for error in _CHANGE_REJECTIONS:
        app.add_exception_handler(error, reject_change)
    # Access is never assumed when Identity cannot answer.
    app.add_exception_handler(IdentityUnavailable, _unavailable("identity_unavailable"))
    for storage_error in (DBAPIError, TimeoutError, OSError):
        app.add_exception_handler(storage_error, _unavailable("tasks_unavailable"))
    return app


app = create_tasks_app(Container())
