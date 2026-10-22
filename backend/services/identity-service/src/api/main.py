from typing import Any

from fastapi import FastAPI
from kernel_platform.http import create_app, register_error_handlers
from sqlalchemy.exc import DBAPIError, TimeoutError

from api.containers import Container
from api.endpoints.admin import router as admin_router
from api.endpoints.auth import router as auth_router
from api.endpoints.members import router as members_router
from api.endpoints.users import router as users_router


def create_identity_app(container: Any = None) -> FastAPI:
    app = create_app("identity", container)
    app.state.container = container
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(members_router)
    app.include_router(admin_router)
    # Storage failures never degrade into access granted on stale claims.
    register_error_handlers(
        app, unavailable_error_types=(DBAPIError, TimeoutError, OSError)
    )
    return app


app = create_identity_app(Container())
