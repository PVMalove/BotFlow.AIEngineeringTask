"""Platform FastAPI exception handlers.

Turn FastAPI `HTTPException`, request validation errors, `ApiError` and unhandled
exceptions into the one error envelope `{"error": {...}}`. A request body that
fails validation answers 422; a value that a domain rule rejects answers 400.
Technical failures (database, Identity) are exceptions, not `Result`: a service
lists their types in `unavailable_error_types` and they answer 503.
"""

import logging
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from typing import cast

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from observability.context import request_id_var
from starlette.exceptions import HTTPException as StarletteHTTPException

from kernel_platform.http.errors import (
    ApiError,
    ErrorBody,
    ErrorDetail,
    ErrorResponse,
    ServiceUnavailableError,
)

logger = logging.getLogger(__name__)

# Both 400 and 422 are validation failures for a client.
_CANONICAL_CODE_OVERRIDES: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: "validation_error",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "validation_error",
}

# Transport prefixes of a Pydantic `loc` are not public field names.
_LOC_TRANSPORT_PREFIXES = {"body", "query", "path", "header", "cookie"}


def register_error_handlers(
    app: FastAPI,
    *,
    unavailable_error_types: Sequence[type[Exception]] = (),
) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(ApiError, _api_error_handler)
    for unavailable_type in unavailable_error_types:
        app.add_exception_handler(unavailable_type, _unavailable_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    http_exc = cast(StarletteHTTPException, exc)
    detail = http_exc.detail
    message = detail if isinstance(detail, str) else "Ошибка запроса"
    return _error_response(
        request,
        status_code=http_exc.status_code,
        code=_canonical_code(http_exc.status_code),
        message=message,
        headers=http_exc.headers,
    )


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    validation_exc = cast(RequestValidationError, exc)
    return _error_response(
        request,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="validation_error",
        message="Некорректные данные запроса",
        details=_details_for_request_validation(validation_exc),
    )


async def _api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    api_error = cast(ApiError, exc)
    return _error_response(
        request,
        status_code=api_error.status_code,
        code=api_error.code,
        message=api_error.message,
        details=api_error.details,
        headers=api_error.headers,
    )


async def _unavailable_handler(request: Request, _exc: Exception) -> JSONResponse:
    return await _api_error_handler(request, ServiceUnavailableError())


async def _unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    logger.exception(
        "Unhandled exception while processing %s", request.url, exc_info=exc
    )
    return _error_response(
        request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="internal_error",
        message="Внутренняя ошибка сервера",
    )


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[ErrorDetail] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    # An unhandled exception is answered outside the request-context middleware,
    # where the context variable is already reset: the id comes from the request.
    request_id = getattr(request.state, "request_id", None) or request_id_var.get()
    body = ErrorResponse(
        error=ErrorBody(
            code=code, message=message, details=details, request_id=request_id
        )
    )
    response_headers = dict(headers or {})
    if request_id is not None:
        response_headers["X-Request-ID"] = request_id
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(exclude_none=True),
        headers=response_headers,
    )


def _canonical_code(status_code: int) -> str:
    if status_code in _CANONICAL_CODE_OVERRIDES:
        return _CANONICAL_CODE_OVERRIDES[status_code]
    try:
        return HTTPStatus(status_code).name.lower()
    except ValueError:
        return "http_error"


def _details_for_request_validation(
    exc: RequestValidationError,
) -> list[ErrorDetail] | None:
    errors = exc.errors()
    if not errors:
        return None
    return [
        ErrorDetail(field=_dot_path(error["loc"]), issue=error["msg"])
        for error in errors
    ]


def _dot_path(loc: Sequence[int | str]) -> str | None:
    """The public dot-path of a Pydantic `loc` without its transport prefix."""
    segments = list(loc)
    if segments and segments[0] in _LOC_TRANSPORT_PREFIXES:
        segments = segments[1:]
    if not segments:
        return None
    return ".".join(str(segment) for segment in segments)
