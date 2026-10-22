"""Платформенные FastAPI exception handlers (ADR 0003).

Нормализуют FastAPI `HTTPException`, request-validation, ожидаемые
service-исключения и неожиданные сбои в единую структурированную
`ErrorResponse`. `register_error_handlers` не импортирует ни одного
service-specific класса — исключение сервиса передаётся параметром, поэтому
kernel_platform не получает зависимость от catalog/identity/support."""

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from http import HTTPStatus
from typing import Protocol, cast

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

# Канонические коды из ADR 0003 — переопределяют HTTPStatus.name там, где
# ADR называет отдельное машиночитаемое имя (400/422 — единый
# VALIDATION_ERROR, а не BAD_REQUEST/UNPROCESSABLE_ENTITY).
_CANONICAL_CODE_OVERRIDES: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: "VALIDATION_ERROR",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "VALIDATION_ERROR",
}

# Транспортные префиксы Pydantic/FastAPI `loc` (ADR 0014) — не публичные
# имена полей, поэтому не должны попадать в `details.field`.
_LOC_TRANSPORT_PREFIXES = {"body", "query", "path", "header", "cookie"}


class _StructuredError(Protocol):
    """Структурная форма, которую должно предоставлять ожидаемое
    service-исключение — код, безопасное сообщение и HTTP-статус."""

    code: str
    message: str
    status_code: int


def _canonical_code(status_code: int) -> str:
    if status_code in _CANONICAL_CODE_OVERRIDES:
        return _CANONICAL_CODE_OVERRIDES[status_code]
    try:
        return HTTPStatus(status_code).name
    except ValueError:
        return "HTTP_ERROR"


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[ErrorDetail] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            details=details,
            request_id=request_id_var.get(),
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(exclude_none=True),
        headers=headers,
    )


def _dot_path(loc: Sequence[int | str]) -> str | None:
    """Pydantic `loc` → публичный dot-path (ADR 0014): ведущий транспортный
    сегмент (`body`/`query`/...) отбрасывается, не являясь именем поля."""
    segments = list(loc)
    if segments and segments[0] in _LOC_TRANSPORT_PREFIXES:
        segments = segments[1:]
    if not segments:
        return None
    return ".".join(str(segment) for segment in segments)


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


async def _http_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    http_exc = cast(StarletteHTTPException, exc)
    detail = http_exc.detail
    message = detail if isinstance(detail, str) else "Ошибка запроса"
    return _error_response(
        status_code=http_exc.status_code,
        code=_canonical_code(http_exc.status_code),
        message=message,
        headers=http_exc.headers,
    )


async def _validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    validation_exc = cast(RequestValidationError, exc)
    return _error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="VALIDATION_ERROR",
        message="Некорректные данные запроса",
        details=_details_for_request_validation(validation_exc),
    )


async def _structured_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    structured = cast(_StructuredError, exc)
    return _error_response(
        status_code=structured.status_code,
        code=structured.code,
        message=structured.message,
        details=getattr(exc, "details", None),
        headers=getattr(exc, "headers", None),
    )


async def _unavailable_handler(_request: Request, _exc: Exception) -> JSONResponse:
    return await _structured_error_handler(_request, ServiceUnavailableError())


def _make_unhandled_exception_handler(
    on_unhandled_exception: Callable[[Exception], None] | None,
) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    async def _unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception(
            "Unhandled exception while processing %s", request.url, exc_info=exc
        )
        if on_unhandled_exception is not None:
            try:
                on_unhandled_exception(exc)
            except Exception:
                logger.warning(
                    "on_unhandled_exception hook itself raised; ignoring",
                    exc_info=True,
                )
        return _error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="INTERNAL_ERROR",
            message="Внутренняя ошибка сервера",
        )

    return _unhandled_exception_handler


def register_error_handlers(
    app: FastAPI,
    *,
    service_error_type: type[Exception] | None = None,
    unavailable_error_types: Sequence[type[Exception]] = (),
    on_unhandled_exception: Callable[[Exception], None] | None = None,
) -> None:
    """Регистрирует platform-owned exception handlers на FastAPI-приложении
    сервиса. `service_error_type` — базовый класс ожидаемых application-
    исключений самого сервиса (например, catalog `ApplicationError`);
    kernel_platform его не импортирует, только принимает здесь.

    `on_unhandled_exception` — опциональный best-effort хук (например,
    метрика "exceptions by type" из `observability.metrics`) на действительно
    неожиданные (500) исключения; kernel_platform не тянет зависимость на
    observability/prometheus, только принимает готовый колбэк."""
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(ApiError, _structured_error_handler)
    if service_error_type is not None:
        app.add_exception_handler(service_error_type, _structured_error_handler)
    for unavailable_type in unavailable_error_types:
        app.add_exception_handler(unavailable_type, _unavailable_handler)
    app.add_exception_handler(
        Exception, _make_unhandled_exception_handler(on_unhandled_exception)
    )
