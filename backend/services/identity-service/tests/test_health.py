"""Process health must never advertise unfinished business features."""

import httpx
import pytest

from botflow_identity.api.main import app


@pytest.mark.asyncio
async def test_process_is_alive_while_business_is_not_ready() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        live = await client.get("/health/live")
        ready = await client.get("/health/ready")

    assert live.status_code == 200
    assert live.json() == {"status": "alive", "service": "identity"}
    assert ready.status_code == 503
    assert ready.json() == {
        "status": "not_ready",
        "reason": "business_adapters_not_wired",
    }
