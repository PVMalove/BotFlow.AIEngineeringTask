"""CSRF guard for state-changing routes: the browser must name our own origin.

The session cookie is SameSite=Strict, and the ingress forwards the browser's
Host and the scheme it was reached by. The same check guards the WebSocket
handshake, which the browser sends with the Origin of the page.
"""

from fastapi import Request
from fastapi.requests import HTTPConnection

from kernel_platform.http.errors import ApiError

# A WebSocket handshake is an HTTP request: the browser names the page's origin.
_PAGE_SCHEME = {"ws": "http", "wss": "https"}


def is_same_origin(connection: HTTPConnection) -> bool:
    origin = connection.headers.get("origin", "").lower()
    return origin == _expected_origin(connection)


async def require_same_origin(request: Request) -> None:
    if not is_same_origin(request):
        raise ApiError(
            status_code=403,
            code="origin_not_allowed",
            message="Запрос отправлен с другого источника.",
        )


def _expected_origin(connection: HTTPConnection) -> str:
    scheme = connection.url.scheme
    scheme = connection.headers.get(
        "x-forwarded-proto", _PAGE_SCHEME.get(scheme, scheme)
    )
    return f"{scheme}://{connection.headers.get('host', '')}".lower()
