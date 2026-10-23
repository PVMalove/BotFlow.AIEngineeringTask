"""Error envelope of the HTTP API and the status code of every `ErrorType`."""

from kernel_domain.errors import Error, ErrorList, ErrorType
from pydantic import BaseModel

_STATUS_BY_ERROR_TYPE: dict[ErrorType, int] = {
    ErrorType.VALIDATION: 400,
    ErrorType.NOT_FOUND: 404,
    ErrorType.CONFLICT: 409,
    ErrorType.FORBIDDEN: 403,
    ErrorType.UNAUTHORIZED: 401,
    ErrorType.RATE_LIMITED: 429,
    ErrorType.PROBLEM: 400,
}


def status_code_for_error_type(error_type: ErrorType) -> int:
    """The HTTP status of a domain `ErrorType`; one table for all services."""
    return _STATUS_BY_ERROR_TYPE[error_type]


class ErrorDetail(BaseModel):
    """One entry of `error.details`: a violation bound to the public field name.
    `field` is absent for an error that belongs to no single field."""

    field: str | None = None
    issue: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    """`code` is machine-readable, `message` is safe to show to the user,
    `details` optionally binds validation errors to fields."""

    error: ErrorBody


def details_for_error(error: Error) -> list[ErrorDetail] | None:
    """`error.details` exists only for an `ErrorList`; a single error has none."""
    if not isinstance(error, ErrorList):
        return None
    return [
        ErrorDetail(field=child.invalid_field, issue=child.description)
        for child in error.errors
    ]


class ApiError(Exception):
    """An error ready to be serialised into `ErrorResponse`: what `match_result`
    and `raise_error` raise for a failed `Result`."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: list[ErrorDetail] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


class ServiceUnavailableError(ApiError):
    """A dependency (database, Identity) cannot answer; the caller may retry."""

    def __init__(self, code: str = "service_unavailable") -> None:
        super().__init__(
            status_code=503,
            code=code,
            message="Сервис временно недоступен. Повторите позже.",
            headers={"Retry-After": "5"},
        )
