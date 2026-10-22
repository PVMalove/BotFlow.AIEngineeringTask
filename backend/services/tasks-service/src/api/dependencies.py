"""FastAPI dependency wiring for the tasks service."""

from typing import Annotated

from fastapi import Depends, Request

from api.containers import Services


async def get_services(request: Request) -> Services:
    services: Services = await request.app.state.container.services.async_()
    return services


ServicesDI = Annotated[Services, Depends(get_services)]

__all__ = ["ServicesDI", "get_services"]
