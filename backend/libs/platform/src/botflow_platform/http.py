"""HTTP process shell; business readiness is deliberately not reported yet."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse


def create_app(service_name: str) -> FastAPI:
    app = FastAPI(title=f"BotFlow · {service_name}", version="0.1.0")

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
