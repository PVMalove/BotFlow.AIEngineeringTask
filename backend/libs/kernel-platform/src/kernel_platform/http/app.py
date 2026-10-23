"""FastAPI application shell shared by BotFlow services."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from observability.formatters import configure_logging
from observability.middleware import RequestContextMiddleware


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

    configure_logging(os.environ.get("APP_ENV", "prod"), service_name)
    app = FastAPI(title=f"BotFlow · {service_name}", version="0.1.0", lifespan=lifespan)
    app.add_middleware(RequestContextMiddleware)

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
