"""Структурированная форма ошибки BFF + маппинг статусов доменного
`Result` (ADR 0002/0003/0014)."""

from kernel_domain.errors import Error, ErrorList, ErrorType
from pydantic import BaseModel

_STATUS_BY_ERROR_TYPE: dict[ErrorType, int] = {
    ErrorType.VALIDATION: 400,
    ErrorType.NOT_FOUND: 404,
    ErrorType.CONFLICT: 409,
    ErrorType.FORBIDDEN: 403,
    ErrorType.UNAUTHORIZED: 401,
    ErrorType.PROBLEM: 400,
    ErrorType.FAILURE: 500,
}


def status_code_for_error_type(error_type: ErrorType) -> int:
    """HTTP-статус для домейного `ErrorType` — единая таблица для всех
    сервисов, не зависящая ни от одного service-specific DTO."""
    return _STATUS_BY_ERROR_TYPE[error_type]


class ErrorDetail(BaseModel):
    """Один элемент `error.details` — привязка нарушения к публичному
    `snake_case`-имени поля запроса (ADR 0014). `field` не передаётся для
    ошибки формы без отдельного поля."""

    field: str | None = None
    issue: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    """Структурированная форма ошибки BFF-контракта: `code` — машиночитаемый,
    `message` — безопасный для показа пользователю текст без деталей
    реализации, `details` — опциональная построчная привязка ошибок
    валидации к полям (ADR 0014)."""

    error: ErrorBody


def details_for_error(error: Error) -> list[ErrorDetail] | None:
    """`error.details` для BFF-конверта (ADR 0014): заполняется только для
    `ErrorList`, одиночная ошибка не получает `details`."""
    if not isinstance(error, ErrorList):
        return None
    return [
        ErrorDetail(field=child.invalid_field, issue=child.description)
        for child in error.errors
    ]


class ApiError(Exception):
    """Ошибка, готовая к сериализации в `ErrorResponse` — то, что выбрасывают
    `match_result`/`match_created` на неуспешном `Result`. Экспонирует
    `code`/`message`/`status_code` структурно — той же формой, которой должны
    следовать ожидаемые service-исключения, перехватываемые
    `register_error_handlers`."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: list[ErrorDetail] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
