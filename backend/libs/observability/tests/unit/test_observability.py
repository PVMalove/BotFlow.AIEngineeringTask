import json
import logging

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from observability.context import request_id_var
from observability.formatters import JsonFormatter, select_formatter
from observability.middleware import RequestContextMiddleware


def _record(message: str = "hello") -> logging.LogRecord:
    return logging.LogRecord("svc", logging.INFO, __file__, 1, message, None, None)


def test_json_formatter_has_fixed_keys_and_request_id() -> None:
    token = request_id_var.set("req-1")
    try:
        payload = json.loads(JsonFormatter("tasks").format(_record()))
    finally:
        request_id_var.reset(token)
    assert payload["service"] == "tasks"
    assert payload["request_id"] == "req-1"
    assert payload["message"] == "hello"
    assert payload["status_code"] is None


def test_select_formatter_prefers_json_outside_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OBSERVABILITY_LOG_FORMAT", raising=False)
    assert isinstance(select_formatter("prod", "tasks"), JsonFormatter)
    assert not isinstance(select_formatter("dev", "tasks"), JsonFormatter)


def _client() -> TestClient:
    async def ping(request: Request) -> PlainTextResponse:
        return PlainTextResponse(request_id_var.get() or "")

    app = Starlette(routes=[Route("/ping", ping)])
    app.add_middleware(RequestContextMiddleware)
    return TestClient(app)


def test_middleware_echoes_valid_request_id() -> None:
    response = _client().get("/ping", headers={"X-Request-ID": "abc-123"})
    assert response.headers["X-Request-ID"] == "abc-123"
    assert response.text == "abc-123"


def test_middleware_replaces_unsafe_request_id() -> None:
    response = _client().get("/ping", headers={"X-Request-ID": "bad id!\n"})
    assert response.headers["X-Request-ID"] != "bad id!\n"
    assert len(response.headers["X-Request-ID"]) == 36


def test_middleware_writes_access_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="observability.middleware"):
        _client().get("/ping")
    record = next(r for r in caplog.records if r.name == "observability.middleware")
    assert record.__dict__["path"] == "/ping"
    assert record.__dict__["status_code"] == 200
