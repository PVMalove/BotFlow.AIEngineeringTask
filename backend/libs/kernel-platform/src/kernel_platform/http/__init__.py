"""HTTP contract shared by BotFlow services: envelope, errors, handlers."""

from kernel_platform.http.app import create_app
from kernel_platform.http.envelope import ApiResponse
from kernel_platform.http.errors import ApiError, ErrorDetail, ErrorResponse
from kernel_platform.http.exception_handlers import register_error_handlers
from kernel_platform.http.match import match_created, match_page, match_result

__all__ = [
    "ApiError",
    "ApiResponse",
    "ErrorDetail",
    "ErrorResponse",
    "create_app",
    "match_created",
    "match_page",
    "match_result",
    "register_error_handlers",
]
