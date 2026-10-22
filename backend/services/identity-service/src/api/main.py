from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from kernel_platform.http import create_app
from sqlalchemy.exc import DBAPIError, TimeoutError

from api.containers import Container
from api.endpoints.admin import router as admin_router
from api.endpoints.auth import router as auth_router
from api.endpoints.members import router as members_router
from api.endpoints.users import router as users_router
from domain.access import (
    CannotBlockSelf,
    InvalidTransition,
    LastAdmin,
    MemberNotFound,
    VersionConflict,
)
from domain.entities.auth import (
    InvalidLoginCode,
    InvalidSession,
    MemberNotApproved,
    RateLimited,
)

# Deliberately coarse: a caller learns why only as far as it can act on it.
_REJECTIONS: dict[type[Exception], tuple[int, str]] = {
    InvalidLoginCode: (401, "invalid_code"),
    InvalidSession: (401, "unauthenticated"),
    MemberNotApproved: (403, "not_approved"),
    RateLimited: (429, "rate_limited"),
    MemberNotFound: (404, "member_not_found"),
    VersionConflict: (409, "version_conflict"),
    InvalidTransition: (409, "invalid_transition"),
    CannotBlockSelf: (409, "cannot_block_self"),
    LastAdmin: (409, "last_admin"),
}


async def reject(request: Request, error: Exception) -> JSONResponse:
    status, detail = _REJECTIONS[type(error)]
    headers = {"Retry-After": "60"} if status == 429 else None
    return JSONResponse({"detail": detail}, status_code=status, headers=headers)


async def unavailable(request: Request, error: Exception) -> JSONResponse:
    """Storage failures never degrade into access granted on stale claims."""
    return JSONResponse(
        {"detail": "identity_unavailable"},
        status_code=503,
        headers={"Retry-After": "5"},
    )


def create_identity_app(container: Any = None) -> FastAPI:
    app = create_app("identity", container)
    app.state.container = container
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(members_router)
    app.include_router(admin_router)
    for error in _REJECTIONS:
        app.add_exception_handler(error, reject)
    app.add_exception_handler(DBAPIError, unavailable)
    app.add_exception_handler(TimeoutError, unavailable)
    app.add_exception_handler(OSError, unavailable)
    return app


app = create_identity_app(Container())
