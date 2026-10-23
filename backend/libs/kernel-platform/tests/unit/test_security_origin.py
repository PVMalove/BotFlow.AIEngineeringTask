"""The browser must name the origin it was reached by, for HTTP and WebSocket."""

import pytest
from starlette.requests import HTTPConnection

from kernel_platform.security.origin import is_same_origin


def connection(
    scheme: str, host: str, origin: str | None, forwarded_proto: str | None = None
) -> HTTPConnection:
    headers = [(b"host", host.encode())]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if forwarded_proto is not None:
        headers.append((b"x-forwarded-proto", forwarded_proto.encode()))
    return HTTPConnection(
        {
            "type": "websocket" if scheme.startswith("ws") else "http",
            "scheme": scheme,
            "server": ("internal", 8000),
            "path": "/",
            "query_string": b"",
            "headers": headers,
        }
    )


@pytest.mark.parametrize(
    "scheme, origin",
    [
        ("http", "http://botflow.test"),
        ("https", "https://botflow.test"),
        ("ws", "http://botflow.test"),
        ("wss", "https://botflow.test"),
        ("http", "HTTP://BotFlow.Test"),
    ],
)
def test_the_origin_of_the_scheme_the_connection_arrived_by_is_accepted(
    scheme: str, origin: str
) -> None:
    assert is_same_origin(connection(scheme, "botflow.test", origin))


@pytest.mark.parametrize(
    "scheme, origin",
    [
        ("http", None),
        ("http", ""),
        ("http", "http://evil.example"),
        ("http", "https://botflow.test"),
        ("ws", "ws://botflow.test"),
        ("wss", "http://botflow.test"),
    ],
)
def test_another_missing_or_other_scheme_origin_is_refused(
    scheme: str, origin: str | None
) -> None:
    assert not is_same_origin(connection(scheme, "botflow.test", origin))


@pytest.mark.parametrize("scheme", ["http", "ws"])
def test_the_scheme_the_proxy_forwarded_decides(scheme: str) -> None:
    assert is_same_origin(
        connection(scheme, "botflow.test", "https://botflow.test", "https")
    )
    assert not is_same_origin(
        connection(scheme, "botflow.test", "http://botflow.test", "https")
    )
