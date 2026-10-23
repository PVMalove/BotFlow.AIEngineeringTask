"""Platform exception handlers turn every failure into `{"error": {...}}`."""

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from observability.middleware import RequestContextMiddleware
from pydantic import BaseModel

from kernel_platform.http.errors import ApiError, ErrorDetail, ServiceUnavailableError
from kernel_platform.http.exception_handlers import register_error_handlers


class _DemoDependencyError(Exception):
    """A technical failure of a dependency; the service lists it as unavailable."""


class _Item(BaseModel):
    price: float


class _Body(BaseModel):
    price: float
    items: list[_Item] = []


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
    assert _without_request_id(response) == {
        "error": {"code": "unauthorized", "message": "Требуется авторизация"}
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
    assert body["error"]["code"] == "validation_error"
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
    assert _without_request_id(response) == {
        "error": {
            "code": "general_multiple_validation_errors",
            "message": "Обнаружены множественные ошибки валидации",
            "details": [
                {"field": "name", "issue": "Плохое имя"},
                {"field": "price", "issue": "Плохая цена"},
            ],
        }
    }


async def test_a_failing_dependency_answers_503_with_retry_after(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        response = await client.get("/dependency-down")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.json()["error"]["code"] == ServiceUnavailableError().code


async def test_unexpected_exception_is_hidden_behind_a_safe_500(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        response = await client.get("/boom")

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert error["message"] == "Внутренняя ошибка сервера"
    assert "secret" not in response.text
    assert "postgres://" not in response.text


async def test_the_500_response_carries_the_request_id_in_body_and_header(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        response = await client.get("/boom", headers={"X-Request-ID": "req-500"})

    assert response.status_code == 500
    assert response.headers["x-request-id"] == "req-500"
    assert response.json()["error"]["request_id"] == "req-500"


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app, unavailable_error_types=[_DemoDependencyError])

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

    @app.get("/dependency-down")
    async def _dependency_down() -> None:
        raise _DemoDependencyError()

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


def _without_request_id(response: httpx.Response) -> dict[str, dict[str, object]]:
    body: dict[str, dict[str, object]] = response.json()
    body["error"].pop("request_id")
    return body
