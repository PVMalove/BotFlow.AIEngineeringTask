"""HTTP process shell; business readiness is deliberately not reported yet."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class ApiResponse[T](BaseModel):
    """Успешный BFF-ответ (ADR 0002/ProductsFlow standard).

    `data` — полезная нагрузка эндпоинта.
    `meta` — метаданные (пагинация, курсоры и т.д.).
    """

    data: T
    meta: dict[str, object] = Field(default_factory=dict)


def _unwrap_result(result: Any) -> Any:
    if hasattr(result, "is_err") and result.is_err:
        error = getattr(result, "error", None)
        message = getattr(error, "description", str(error))
        status_code = getattr(error, "status_code", 400)
        raise HTTPException(status_code=status_code, detail=message)
    return result.value if hasattr(result, "value") else result


def match_result[T](result: Any) -> ApiResponse[T]:
    """Разворачивание результата выполнения команды/запроса в ApiResponse."""
    return ApiResponse(data=_unwrap_result(result))


def match_created[T](result: Any) -> ApiResponse[T]:
    """Семантический алиас match_result для create-эндпоинтов."""
    return match_result(result)


def match_page[T](result: Any) -> ApiResponse[list[T]]:
    """Разворачивание пагинированного результата в ApiResponse[list[T]]."""
    value = _unwrap_result(result)
    if hasattr(value, "items"):
        items = list(value.items)
        page_info = getattr(value, "page_info", None)
        meta = (
            {
                "next_cursor": getattr(page_info, "next_cursor", None),
                "prev_cursor": getattr(page_info, "prev_cursor", None),
                "has_more": getattr(page_info, "has_more", False),
                "has_prev": getattr(page_info, "has_prev", False),
            }
            if page_info is not None
            else {}
        )
        return ApiResponse(data=items, meta=meta)
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], dict):
        return ApiResponse(data=list(value[0]), meta=value[1])
    return ApiResponse(data=list(value), meta={})


def create_app(service_name: str, container: Any = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if container is None:
            yield
            return
        try:
            await container.init_resources()
            app.state.resources = await container.resources()
            yield
        finally:
            await container.shutdown_resources()

    app = FastAPI(title=f"BotFlow · {service_name}", version="0.1.0", lifespan=lifespan)

    @app.get("/health/live", include_in_schema=False)
    def liveness() -> dict[str, str]:
        return {"status": "alive", "service": service_name}

    @app.get("/health/ready", include_in_schema=False)
    def readiness() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "business_adapters_not_wired"},
        )

    return app


__all__ = [
    "ApiResponse",
    "create_app",
    "match_created",
    "match_page",
    "match_result",
]
