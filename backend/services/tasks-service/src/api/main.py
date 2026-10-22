from typing import Any

from botflow_platform.http import create_app
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, TimeoutError

from api.containers import Container
from api.endpoints.tasks import router as tasks_router
from application.ports import (
    IdentityUnavailable,
    InvalidCursor,
    NotApproved,
    TaskNotFound,
    Unauthenticated,
)

# Deliberately coarse: a caller learns why only as far as it can act on it.
_REJECTIONS: dict[type[Exception], tuple[int, str]] = {
    Unauthenticated: (401, "unauthenticated"),
    NotApproved: (403, "not_approved"),
    InvalidCursor: (400, "invalid_cursor"),
    TaskNotFound: (404, "task_not_found"),
}


async def reject(request: Request, error: Exception) -> JSONResponse:
    status, detail = _REJECTIONS[type(error)]
    return JSONResponse({"detail": detail}, status_code=status)


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
    # Access is never assumed when Identity cannot answer.
    app.add_exception_handler(IdentityUnavailable, _unavailable("identity_unavailable"))
    for storage_error in (DBAPIError, TimeoutError, OSError):
        app.add_exception_handler(storage_error, _unavailable("tasks_unavailable"))
    return app


app = create_tasks_app(Container())
