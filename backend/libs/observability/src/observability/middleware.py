import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from observability.context import request_id_var

logger = logging.getLogger(__name__)

_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Binds `request_id` for the request, echoes it in `X-Request-ID` and
    writes one access-log line per request."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID.fullmatch(incoming) else str(uuid.uuid4())
        # The handler of an unhandled exception runs outside this middleware, after
        # the context variable is reset, so it reads the id from the request state.
        request.state.request_id = request_id
        reset = request_id_var.set(request_id)
        started_at = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            logger.info(
                "%s %s request_id=%s",
                request.method,
                request.url.path,
                request_id,
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": (time.perf_counter() - started_at) * 1000,
                },
            )
            request_id_var.reset(reset)
