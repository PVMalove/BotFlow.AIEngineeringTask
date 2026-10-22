from typing import Any

from fastapi import FastAPI
from kernel_platform.http.app import create_app
from kernel_platform.http.exception_handlers import register_error_handlers
from sqlalchemy.exc import DBAPIError, TimeoutError

from api.containers import Container
from api.endpoints.tasks import router as tasks_router
from application.ports import IdentityUnavailable


def create_tasks_app(container: Any = None) -> FastAPI:
    app = create_app("tasks", container)
    app.state.container = container
    app.include_router(tasks_router)
    # Access is never assumed when Identity or the database cannot answer.
    register_error_handlers(
        app,
        unavailable_error_types=(
            IdentityUnavailable,
            DBAPIError,
            TimeoutError,
            OSError,
        ),
    )
    return app


app = create_tasks_app(Container())
