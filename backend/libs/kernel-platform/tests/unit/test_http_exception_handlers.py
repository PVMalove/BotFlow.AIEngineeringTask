"""ADR 0003: платформенные exception handlers нормализуют любую форму
отказа в структурированный конверт `{"error": {"code", "message"}}`."""

from collections.abc import Callable

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from kernel_platform.http.errors import ApiError, ErrorDetail
from kernel_platform.http.exception_handlers import register_error_handlers


class _DemoServiceError(Exception):
    """Заглушка вместо собственного базового `ApplicationError` сервиса
    (структурный code/message/status_code, без наследования от kernel_platform)."""

    code = "DEMO_UNAVAILABLE"
    message = "demo service недоступен"
    status_code = 503

    def __init__(self) -> None:
        super().__init__(self.message)


class _Item(BaseModel):
    price: float


class _Body(BaseModel):
    price: float
    items: list[_Item] = []


def _build_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app, service_error_type=_DemoServiceError)

    @app.get("/http-error")
    async def _http_error() -> None:
        raise HTTPException(status_code=401, detail="Требуется авторизация")

    @app.get("/http-error-with-headers")
    async def _http_error_with_headers() -> None:
        raise HTTPException(
            status_code=401,
            detail="Требуется авторизация",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.post("/validate")
    async def _validate(body: _Body) -> dict[str, float]:
        return {"price": body.price}

    @app.get("/service-error")
    async def _service_error() -> None:
        raise _DemoServiceError()

    @app.get("/api-error-with-details")
    async def _api_error_with_details() -> None:
        raise ApiError(
            status_code=400,
            code="general_multiple_validation_errors",
            message="Обнаружены множественные ошибки валидации",
            details=[
                ErrorDetail(field="name", issue="Плохое имя"),
                ErrorDetail(field="price", issue="Плохая цена"),
            ],
        )

    @app.get("/boom")
    async def _boom() -> None:
        raise RuntimeError("connection string: postgres://secret@host/db")

    return app


@pytest.fixture
def client() -> httpx.AsyncClient:
    # raise_app_exceptions=False: /boom намеренно позволяет необработанному
    # исключению дойти до платформенного catch-all — без этого ASGITransport
    # поднимет его заново в тесте вместо возврата 500, который строит handler.
    transport = httpx.ASGITransport(app=_build_app(), raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_http_exception_gets_canonical_code_and_original_status(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        response = await client.get("/http-error")

    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "UNAUTHORIZED", "message": "Требуется авторизация"}
    }


async def test_http_exception_headers_are_preserved(client: httpx.AsyncClient) -> None:
    async with client:
        response = await client.get("/http-error-with-headers")

    assert response.headers["www-authenticate"] == "Bearer"


async def test_request_validation_error_becomes_structured_400_shape(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        response = await client.post("/validate", json={"price": "not-a-number"})

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["message"] == "Некорректные данные запроса"


async def test_request_validation_error_details_use_dot_path_without_transport_prefix(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        response = await client.post(
            "/validate",
            json={"price": "not-a-number", "items": [{"price": "also-bad"}]},
        )

    fields = {detail["field"] for detail in response.json()["error"]["details"]}
    assert fields == {"price", "items.0.price"}
    for detail in response.json()["error"]["details"]:
        assert "body" not in detail["field"]


async def test_request_validation_error_omits_field_for_form_level_violations(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        response = await client.post("/validate", content=b"not json")

    details = response.json()["error"]["details"]
    assert len(details) == 1
    assert "field" not in details[0]
    assert "issue" in details[0]


async def test_api_error_with_details_includes_them_in_the_response(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        response = await client.get("/api-error-with-details")

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "general_multiple_validation_errors",
            "message": "Обнаружены множественные ошибки валидации",
            "details": [
                {"field": "name", "issue": "Плохое имя"},
                {"field": "price", "issue": "Плохая цена"},
            ],
        }
    }


async def test_expected_service_error_uses_its_own_structural_contract(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        response = await client.get("/service-error")

    assert response.status_code == 503
    assert response.json() == {
        "error": {"code": "DEMO_UNAVAILABLE", "message": "demo service недоступен"}
    }


async def test_unexpected_exception_is_hidden_behind_a_safe_500(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        response = await client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert body == {
        "error": {"code": "INTERNAL_ERROR", "message": "Внутренняя ошибка сервера"}
    }
    assert "secret" not in response.text
    assert "postgres://" not in response.text


def _build_app_with_boom(
    on_unhandled_exception: Callable[[Exception], None] | None,
) -> FastAPI:
    app = FastAPI()
    register_error_handlers(
        app,
        service_error_type=_DemoServiceError,
        on_unhandled_exception=on_unhandled_exception,
    )

    @app.get("/boom")
    async def _boom() -> None:
        raise RuntimeError("kaboom")

    return app


async def test_on_unhandled_exception_hook_receives_the_exception() -> None:
    received: list[Exception] = []
    transport = httpx.ASGITransport(
        app=_build_app_with_boom(received.append), raise_app_exceptions=False
    )

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    assert len(received) == 1
    assert isinstance(received[0], RuntimeError)


async def test_on_unhandled_exception_hook_fail_does_not_break_the_500_response() -> (
    None
):
    def _broken_hook(exc: Exception) -> None:
        raise ValueError("hook itself is broken")

    transport = httpx.ASGITransport(
        app=_build_app_with_boom(_broken_hook), raise_app_exceptions=False
    )

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "Внутренняя ошибка сервера"}
    }
